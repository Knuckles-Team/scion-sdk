"""The session activation gate: lazy namespaces list nothing until revealed (R914.7, R914.11).

A gate wraps one upstream's provider. While the current session has not
activated the namespace, it returns empty component lists — so a hidden
namespace lists no components and starts no process. The first activation
lists the upstream. An ``active`` namespace (a gateway deployment) always
lists; its sessions may still hide it from their own view. Under an
``on_drift: refuse`` policy a drifted namespace lists nothing and its calls
refuse with ``UPSTREAM_DRIFT_REFUSED``. A listing that fails marks the
upstream ``unavailable``; under ``raise`` the failure also fails the list,
under ``warn`` the namespace lists nothing while the rest of the server keeps
serving.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.base import Provider
from fastmcp.server.transforms.visibility import get_visibility_rules

from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_DRIFT_REFUSED,
    UPSTREAM_SPAWN_FAILED,
    UpstreamDecl,
)

if TYPE_CHECKING:
    from scion_sdk.mcp.upstreams.exchange import ExchangeRuntime
    from scion_sdk.mcp.upstreams.pool import UpstreamPool

__all__ = ["ActivationGate", "session_active_namespaces"]

_logger = logging.getLogger(__name__)


def _failure_code(exc: Exception) -> str:
    """The refusal code a listing failure records: a token, never error text."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) else UPSTREAM_SPAWN_FAILED


async def session_active_namespaces() -> set[str]:
    """The namespaces the current session has activated, from its rules."""
    context = get_context()
    rules = await get_visibility_rules(context)
    states: dict[str, bool] = {}
    for rule in rules:
        for tag in rule.get("tags") or ():
            if isinstance(tag, str) and tag.startswith("ns:"):
                states[tag[3:]] = bool(rule.get("enabled"))
    return {namespace for namespace, enabled in states.items() if enabled}


def _drift_refusal_tool(name: str, namespace: str) -> Any:
    """A stand-in tool whose calls refuse with ``UPSTREAM_DRIFT_REFUSED``."""
    from fastmcp.exceptions import ToolError
    from fastmcp.tools import Tool

    class _Refusal(Tool):
        def __init__(self) -> None:
            super().__init__(
                name=name,
                description="Refused: the upstream drifted from its pinned contract.",
                parameters={},
            )

        async def _run(self, arguments: Any) -> Any:
            raise ToolError(f"{UPSTREAM_DRIFT_REFUSED}: {namespace}")

    return _Refusal()


class ActivationGate(Provider):
    """Lists one upstream's components only while its namespace is active."""

    def __init__(
        self,
        inner: Provider,
        decl: UpstreamDecl,
        *,
        registry: Any,
        activation: str,
        pool: UpstreamPool | None = None,
        exchange: ExchangeRuntime | None = None,
    ) -> None:
        super().__init__()
        self._exchange = exchange
        self._inner = inner
        self._decl = decl
        self._registry = registry
        self._activation = activation
        self._pool = pool

    async def _active(self) -> bool:
        if self._activation == "active":
            return True
        try:
            return self._decl.effective_namespace in await session_active_namespaces()
        except RuntimeError:
            return False  # no session: nothing is activated, fail closed

    async def list_tools(self) -> Sequence[Any]:
        if not await self._active():
            return []
        if self._registry.drift_refuses(self._decl):
            return []
        try:
            tools = await self._inner.list_tools()
        except Exception as exc:
            await self._registry.note_failure(self._decl, _failure_code(exc))
            if self._registry.error_strategy == "raise":
                raise
            _logger.warning(
                "upstream %s is unavailable", self._decl.effective_namespace
            )
            return []
        report = await self._registry.note_listed(
            self._decl, tools, await self._version()
        )
        if report.is_drifted:
            await self._notify_list_changed()
        return tools

    async def get_tool(self, name: str, version: Any = None) -> Any:
        if not await self._active():
            return None
        if self._registry.drift_refuses(self._decl):
            return _drift_refusal_tool(name, self._decl.effective_namespace)
        tool = await self._inner.get_tool(name, version=version)
        if tool is None:
            return None
        from fastmcp.server.providers.proxy import ProxyTool

        from scion_sdk.mcp.upstreams.elicitation import ForwardingProxyTool

        if isinstance(tool, ProxyTool):
            if self._exchange is not None and self._decl.exchange is not None:
                from scion_sdk.mcp.upstreams.exchange import ExchangeProxyTool

                return ExchangeProxyTool.bind(tool, self._decl, self._exchange)
            return ForwardingProxyTool.from_proxy_tool(tool)
        return tool

    async def list_resources(self) -> Sequence[Any]:
        if not await self._active() or self._registry.drift_refuses(self._decl):
            return []
        return await self._inner.list_resources()

    async def get_resource(self, uri: str, version: Any = None) -> Any:
        if not await self._active() or self._registry.drift_refuses(self._decl):
            return None
        return await self._inner.get_resource(uri, version=version)

    async def list_resource_templates(self) -> Sequence[Any]:
        if not await self._active() or self._registry.drift_refuses(self._decl):
            return []
        return await self._inner.list_resource_templates()

    async def get_resource_template(self, uri: str, version: Any = None) -> Any:
        if not await self._active() or self._registry.drift_refuses(self._decl):
            return None
        return await self._inner.get_resource_template(uri, version=version)

    async def list_prompts(self) -> Sequence[Any]:
        if not await self._active() or self._registry.drift_refuses(self._decl):
            return []
        return await self._inner.list_prompts()

    async def get_prompt(self, name: str, version: Any = None) -> Any:
        if not await self._active() or self._registry.drift_refuses(self._decl):
            return None
        return await self._inner.get_prompt(name, version=version)

    # -- probing (background; never wakes an inactive namespace) ---------------

    async def probe(self) -> tuple[Sequence[Any], str | None]:
        """Re-list the namespace for the prober; active namespaces only."""
        tools = await self._inner.list_tools()
        return tools, await self._version()

    async def _version(self) -> str | None:
        if self._pool is None:
            return None
        from scion_sdk.mcp.upstreams.pool import current_principal

        try:
            return await self._pool.server_version(self._decl, current_principal())
        except Exception:
            return None

    async def _notify_list_changed(self) -> None:
        """A fingerprint change re-reads the tools at the fleet gateway."""
        try:
            context = get_context()
        except RuntimeError:
            return
        import mcp_types

        await context.send_notification(mcp_types.ToolListChangedNotification())

    def required_extensions(self) -> Sequence[Any]:
        return self._inner.required_extensions()

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None, None]:
        if self._pool is not None:
            self._pool.start_reaper()
        try:
            async with self._inner.lifespan():
                yield
        finally:
            if self._pool is not None:
                await self._pool.stop_reaper()
                await self._pool.close()
