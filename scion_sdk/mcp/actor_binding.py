"""Bind the request's actor once, in the served MCP stack.

This is the one actor-resolution path for every tool call, whatever the
transport. Every connector built by :func:`scion_sdk.mcp.server.create_mcp_server`
gets this middleware on stdio and on streamable HTTP alike, configured the same
way. A server without it resolves the same actor lazily through
:func:`scion_sdk.identity.current_actor`, so no bootstrap ``use_actor`` call is
ever needed.

The transport only supplies the credential;
:func:`~scion_sdk.identity.transport.request_actor` turns it into
an actor, and tools and entitlements read that actor and never the transport:

* a local transport (stdio or an in-process client) is the trusted local
  principal, :data:`~scion_sdk.identity.SYSTEM_ACTOR`;
* an HTTP request carries the principal its verified access token names, with
  the IdP's roles and groups (generic, Okta and Keycloak claim locations);
* an HTTP request to a server the operator started with auth type ``none``
  carries no credential and is the same trusted local principal as stdio,
  :data:`~scion_sdk.identity.SYSTEM_ACTOR`;
* an HTTP request without a verified token to any other server binds
  :data:`~scion_sdk.identity.ANONYMOUS_ACTOR`, which holds no entitlements. A
  factory server with an auth provider answers such a request 401 first.

An :class:`~scion_sdk.identity.IdentityRequiredError` raised under the
binding reaches the client as a typed refusal, never as an internal error.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError

from scion_sdk.identity import IdentityRequiredError, use_actor
from scion_sdk.identity.transport import request_actor

__all__ = ["ActorBindingMiddleware"]

#: JSON-RPC server error code for an identity refusal (the code FastMCP uses
#: for a permission denial).
IDENTITY_REFUSAL_CODE = -32000


def _identity_error(error: BaseException) -> IdentityRequiredError | None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, IdentityRequiredError):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


async def _call_typed(context: MiddlewareContext[Any], call_next: Any) -> Any:
    try:
        return await call_next(context)
    except Exception as error:
        refusal = _identity_error(error)
        if refusal is None:
            raise
        raise MCPError(
            IDENTITY_REFUSAL_CODE,
            f"{refusal.reason}: {refusal}",
            {"reason": refusal.reason},
        ) from error


class ActorBindingMiddleware(Middleware):
    """Bind :func:`request_actor` around every request; refuse identity errors typed.

    ``no_auth`` is true when the operator chose auth type ``none``.
    """

    def __init__(self, *, no_auth: bool = False) -> None:
        self.no_auth = no_auth

    async def on_request(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        with use_actor(request_actor(no_auth=self.no_auth)):
            return await _call_typed(context, call_next)
