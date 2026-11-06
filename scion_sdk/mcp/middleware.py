"""The standard connector middleware stack.

One stack for every transport (SCION-CONNECTOR-CONTROL-R025.6): privacy-safe
error handling, then the actor binding, then a per-actor rate limit. The rate
limiter keys on the actor the binding resolved through
:func:`scion_sdk.identity.current_actor`, never on its own credential lookup, so
a stdio caller and an HTTP caller with the same principal share one bucket and
one identity decision. The trusted local principal
(:data:`~scion_sdk.identity.SYSTEM_ACTOR`: stdio, an in-process client, or HTTP
with auth type ``none``) is not throttled; every other actor gets its own bucket.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastmcp.exceptions import FastMCPError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware
from mcp.shared.exceptions import MCPError

from scion_sdk.identity import ActorType, current_actor
from scion_sdk.mcp.actor_binding import ActorBindingMiddleware

__all__ = [
    "ActorRateLimitingMiddleware",
    "TypedRefusalErrorHandlingMiddleware",
    "build_middleware",
    "caller_bucket",
    "rate_limit_client_id",
]


#: JSON-RPC server error code a typed refusal carries when it names none.
REFUSAL_CODE = -32000


class TypedRefusalErrorHandlingMiddleware(ErrorHandlingMiddleware):
    """Error handling that lets a typed refusal reach the caller as itself.

    FastMCP's stock handler rewrites every exception it does not know exactly
    to ``-32603 Internal error``. A refusal is an answer, not a crash, so it
    passes through with its code and message:

    * a ``ToolError`` (or other ``FastMCPError``) a tool raised on purpose, which
      carries no wrapped cause, is re-raised unchanged;
    * a ``PermissionError`` subclass (an authorization or identity refusal) is
      ``-32000`` with its message;
    * an exception that names its own integer ``code`` and string ``reason`` is
      that code, ``"<reason>: <message>"`` and ``{"reason": <reason>}``.

    Any other exception, including an unexpected failure FastMCP wrapped in a
    ``ToolError``, still becomes an internal error.
    """

    def _transform_error(
        self, error: Exception, context: MiddlewareContext
    ) -> Exception:
        if isinstance(error, MCPError):
            return error
        cause = error.__cause__
        if isinstance(error, FastMCPError) and cause is None:
            return error
        origin = cause if cause is not None else error
        code = getattr(origin, "code", None)
        reason = getattr(origin, "reason", None)
        if (
            isinstance(code, int)
            and not isinstance(code, bool)
            and isinstance(reason, str)
        ):
            return MCPError(code, f"{reason}: {origin}", {"reason": reason})
        if isinstance(origin, PermissionError):
            return MCPError(REFUSAL_CODE, f"Permission denied: {origin}")
        return super()._transform_error(error, context)


def caller_bucket(client_id: object, claims: object) -> str:
    """The rate-limit bucket for a caller identity (``anonymous`` when empty)."""
    mapping = claims if isinstance(claims, dict) else {}
    identity = "\x00".join(
        str(value or "")
        for value in (client_id, mapping.get("sub"), mapping.get("tenant_id"))
    )
    if not identity.strip("\x00"):
        return "anonymous"
    digest = hashlib.blake2s(identity.encode("utf-8"), digest_size=16).hexdigest()
    return f"caller_{digest}"


def rate_limit_client_id(_context: Any) -> str:
    """Rate-limit key for the current request: the bound actor's bucket."""
    actor = current_actor()
    if actor.actor_type is ActorType.ANONYMOUS and not actor.authenticated:
        return "anonymous"
    return caller_bucket(actor.actor_id, {"tenant_id": actor.tenant_id})


def _trusted_local() -> bool:
    actor = current_actor()
    return actor.actor_type is ActorType.SYSTEM and actor.authenticated


class ActorRateLimitingMiddleware(RateLimitingMiddleware):
    """Per-actor token bucket that never throttles the trusted local principal."""

    async def on_request(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        if _trusted_local():
            return await call_next(context)
        return await super().on_request(context, call_next)


def build_middleware(*, no_auth: bool = False) -> list[Any]:
    """Error handling without tracebacks, the actor binding, then 10 requests/s per actor (burst 20).

    ``no_auth`` is true when the network config's auth type is ``none``. The
    returned stack has the same classes in the same order for every transport.
    """
    return [
        TypedRefusalErrorHandlingMiddleware(
            include_traceback=False, transform_errors=True
        ),
        ActorBindingMiddleware(no_auth=no_auth),
        ActorRateLimitingMiddleware(
            max_requests_per_second=10.0,
            burst_capacity=20,
            get_client_id=rate_limit_client_id,
        ),
    ]
