"""Serve a connector's ``connector://resources`` declaration on its MCP server.

SCION-CONNECTOR-CONTROL-R075: the body is assembled from the registered tools'
source declarations (:mod:`scion_sdk.ingest.live_declaration`) each time the
resource is read.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from scion_sdk.ingest.live_declaration import build_declaration
from scion_sdk.manifest.resource_descriptor import RESOURCES_URI
from scion_sdk.manifest.sources import IngestSourceDeclaration
from scion_sdk.mcp._tool_surface_base import registered_tools

__all__ = ["register_live_declaration"]


def register_live_declaration(
    mcp: Any,
    connector: str,
    descriptors: Callable[[], list[dict[str, Any]]] | None = None,
    *,
    declared: Mapping[str, Any] | None = None,
    surface_summary: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]]
    | None = None,
) -> None:
    """Serve ``connector://resources``, computed when read.

    The body lists the sources declared on the registered tools, their
    ontology mappings and, when ``descriptors`` is given, the data-resource
    descriptors. ``declared`` is the package's ``connector_sources`` module. A server that already serves the URI keeps its resource.
    """
    served = getattr(getattr(mcp, "_local_provider", None), "_components", {})
    if any(RESOURCES_URI in str(key) for key in served):
        return

    async def resources() -> str:
        """The data sources and resources this connector declares, as JSON."""
        current_descriptors = descriptors() if descriptors is not None else []
        declaration = build_declaration(
            connector,
            registered_tools(mcp).values(),
            current_descriptors,
            declared=declared,
        )
        if surface_summary is not None:
            declaration = IngestSourceDeclaration.model_validate(
                {
                    **declaration.model_dump(),
                    "surfaces": await surface_summary(current_descriptors),
                }
            )
        return json.dumps(
            declaration.model_dump(mode="json", exclude_unset=True), sort_keys=True
        )

    mcp.resource(
        RESOURCES_URI,
        name="connector-resources",
        mime_type="application/json",
        description="Data sources, ontology mappings and resources of this connector.",
    )(resources)
