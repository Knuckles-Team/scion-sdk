"""The declarative upstream MCP registry behind one connector server.

A connector declares upstream MCP servers as :class:`UpstreamDecl` values on
its ``Connector``; :func:`attach_upstreams` validates, namespaces, pools and
proxies them behind the connector's own server. The registry names no vendor.
"""

from scion_sdk.mcp.upstreams.loader import attach_upstreams, read_registry_settings
from scion_sdk.mcp.upstreams.model import (
    REFUSAL_CODES,
    BridgeDecl,
    SandboxRoots,
    UpstreamDecl,
    UpstreamDeclError,
    UpstreamError,
    UpstreamStatus,
    is_reserved_namespace,
    validate_declarations,
)

__all__ = [
    "REFUSAL_CODES",
    "BridgeDecl",
    "SandboxRoots",
    "UpstreamDecl",
    "UpstreamDeclError",
    "UpstreamError",
    "UpstreamStatus",
    "attach_upstreams",
    "is_reserved_namespace",
    "read_registry_settings",
    "validate_declarations",
]
