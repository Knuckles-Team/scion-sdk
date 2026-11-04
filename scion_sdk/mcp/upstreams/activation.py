"""The four registry tools: namespaces start hidden, activation is per session (R914.7).

``registry_activate`` enables the namespace's tag for the session; the
framework's visibility rules then reveal the namespace's components to that
session only. ``registry_deactivate`` hides them again. The tools change one
session's view; they never call an upstream tool, and a session may activate
at most ``max_active_namespaces`` namespaces.
"""

from __future__ import annotations

from typing import Any

from fastmcp.server.context import Context

from scion_sdk.mcp.upstreams.gate import session_active_namespaces
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_ACTIVE_CAP,
    UPSTREAM_DISABLED,
    UPSTREAM_NAMESPACE_UNKNOWN,
    UpstreamError,
)

__all__ = ["REGISTRY_TOOL_NAMES", "attach_registry_tools"]

REGISTRY_TOOL_NAMES: tuple[str, ...] = (
    "registry_list_namespaces",
    "registry_activate",
    "registry_deactivate",
    "registry_status",
)


def attach_registry_tools(
    server: Any,
    registry: Any,
    *,
    max_active_namespaces: int,
) -> None:
    """Add the four registry tools to ``server`` under their reserved names."""

    async def list_namespaces() -> list[dict[str, Any]]:
        """List every upstream namespace with its state and tool count."""
        return [
            {
                "namespace": status.namespace,
                "state": status.state,
                "tool_count": status.tool_count,
            }
            for status in registry.statuses()
        ]

    async def activate(namespace: str, context: Context) -> dict[str, Any]:
        """Reveal one namespace to this session; the first activation lists it."""
        decl = registry.declaration(namespace)
        if decl is None:
            raise UpstreamError(UPSTREAM_NAMESPACE_UNKNOWN, namespace)
        if not decl.enabled:
            raise UpstreamError(UPSTREAM_DISABLED, namespace)
        if registry.activation_for(decl) == "active":
            return {"namespace": namespace, "state": "active", "changed": False}
        active = await session_active_namespaces()
        if namespace in active:
            return {"namespace": namespace, "state": "active", "changed": False}
        if len(active) >= max_active_namespaces:
            raise UpstreamError(
                UPSTREAM_ACTIVE_CAP,
                f"{len(active)} namespaces are active (cap {max_active_namespaces})",
            )
        await context.enable_components(tags={f"ns:{namespace}"})
        return {"namespace": namespace, "state": "active", "changed": True}

    async def deactivate(namespace: str, context: Context) -> dict[str, Any]:
        """Hide one namespace from this session again."""
        decl = registry.declaration(namespace)
        if decl is None:
            raise UpstreamError(UPSTREAM_NAMESPACE_UNKNOWN, namespace)
        await context.disable_components(tags={f"ns:{namespace}"})
        return {"namespace": namespace, "state": "inactive", "changed": True}

    async def status() -> list[dict[str, Any]]:
        """The health of every upstream: state, counts, fingerprint, version."""
        return [
            status_entry.model_dump(mode="json") for status_entry in registry.statuses()
        ]

    server.tool(name=REGISTRY_TOOL_NAMES[0])(list_namespaces)
    server.tool(name=REGISTRY_TOOL_NAMES[1])(activate)
    server.tool(name=REGISTRY_TOOL_NAMES[2])(deactivate)
    server.tool(name=REGISTRY_TOOL_NAMES[3])(status)
