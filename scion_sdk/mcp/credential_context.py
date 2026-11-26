"""Bind the verified request context to every tool call (SCION-CONNECTOR-CONTROL-R035.3).

The host supplies a binder: given the request's verified actor and the tool
name it returns the :class:`CredentialAccessBinding` the host's authority
granted, or ``None``. The binding is visible to
:meth:`~scion_sdk.credentials.provider.CredentialProvider.get_secret` for the
duration of the call only; a request parameter never supplies it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from scion_sdk.credentials.binding import (
    CredentialAccessBinding,
    use_credential_binding,
)
from scion_sdk.identity import ActorContext, current_actor

__all__ = ["CredentialBinder", "CredentialBindingMiddleware", "bind_credential_context"]

CredentialBinder = Callable[[ActorContext, str], CredentialAccessBinding | None]


class CredentialBindingMiddleware(Middleware):
    """Bind the host-granted credential binding around each ``tools/call``."""

    def __init__(self, binder: CredentialBinder) -> None:
        self._binder = binder

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        tool = str(getattr(context.message, "name", ""))
        with use_credential_binding(self._binder(current_actor(), tool)):
            return await call_next(context)


def bind_credential_context(mcp: Any, binder: CredentialBinder) -> None:
    """Install the binding middleware on ``mcp``."""
    mcp.add_middleware(CredentialBindingMiddleware(binder))
