"""Read the two inputs of a release identity from a live MCP session.

SCION-CONNECTOR-CONTROL-R650.1.2 and R650.1.3: the declaration served at
``connector://resources`` and every ``ontology://`` and ``shapes://`` body,
merged into one graph. The signer and the verifier read them the same way.
"""

from __future__ import annotations

from rdflib import Graph

from scion_sdk.manifest.resource_descriptor import RESOURCES_URI
from scion_sdk.manifest.sources import IngestSourceDeclaration
from scion_sdk.ports.session import McpSession

__all__ = ["read_release_inputs"]

_GRAPH_SCHEMES = ("ontology://", "shapes://")


async def read_release_inputs(
    session: McpSession,
) -> tuple[IngestSourceDeclaration, Graph]:
    """The live declaration and canonical ontology graph of the served connector.

    A connector that serves no ``connector://resources`` declares nothing, so
    its declaration is the empty one under the served server name. Raises ``ValueError`` on a body that does
    not parse.
    """
    resources = await session.list_resources()
    uris = sorted(str(getattr(resource, "uri", "")) for resource in resources)
    if RESOURCES_URI in uris:
        declaration = IngestSourceDeclaration.model_validate_json(
            await session.read_resource(RESOURCES_URI)
        )
    else:
        declaration = IngestSourceDeclaration(
            connector=(await session.server_identity()).name
        )
    graph = Graph()
    for uri in uris:
        if uri.startswith(_GRAPH_SCHEMES):
            graph.parse(data=await session.read_resource(uri), format="turtle")
    return declaration, graph
