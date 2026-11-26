"""Tool declaration of a resource protocol.

SCION-CONNECTOR-CONTROL-R911. A tool optionally declares the protocol of the
resource it acts on and the parameter that holds the resource reference, beside
the actor-required key of R902.1. Both are set together or not at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "RESOURCE_PARAM_META",
    "RESOURCE_PROTOCOLS",
    "RESOURCE_PROTOCOL_META",
    "ToolDeclarationInvalid",
    "resource_declaration",
]

RESOURCE_PROTOCOL_META = "team.knuckles/resource_protocol"
RESOURCE_PARAM_META = "team.knuckles/resource_param"
RESOURCE_PROTOCOLS = frozenset({"web", "ssh", "rdp", "database"})


class ToolDeclarationInvalid(ValueError):
    """The tool declaration is refused (``TOOL_DECLARATION_INVALID``)."""

    code = "TOOL_DECLARATION_INVALID"


def resource_declaration(meta: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """The ``(protocol, parameter)`` a tool declares, or ``None`` when neither is set."""
    meta = meta or {}
    protocol = meta.get(RESOURCE_PROTOCOL_META)
    param = meta.get(RESOURCE_PARAM_META)
    if protocol is None and param is None:
        return None
    if protocol not in RESOURCE_PROTOCOLS or not isinstance(param, str) or not param:
        raise ToolDeclarationInvalid(
            "resource_protocol and resource_param are set together, protocol known"
        )
    return protocol, param
