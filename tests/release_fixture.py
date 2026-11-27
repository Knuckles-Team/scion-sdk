"""A tiny connector package served in process, reporting an installed wheel digest or none."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from release_support import EVIDENCE

from scion_sdk.artifacts.prompts import PromptArtifactKind
from scion_sdk.artifacts.resources import ResourceArtifactKind
from scion_sdk.artifacts.skills import SkillArtifactKind
from scion_sdk.manifest.release_digest import ReleaseDigestError, ReleaseEvidence
from scion_sdk.mcp.content import ConnectorContent, register_connector_content
from scion_sdk.ports.session import McpSession, TransportEndpoint
from scion_sdk.transports.mcp import McpTransport

CONNECTOR = "demo-agent"
TURTLE = (
    "@prefix ex: <http://example.test/> .\n"
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "ex:Thing a owl:Class .\n"
)
KINDS = (SkillArtifactKind(), PromptArtifactKind({}), ResourceArtifactKind())


def package(root: Path) -> ConnectorContent:
    """Write the package's ontology and return its content description."""
    (root / "ontology").mkdir(parents=True, exist_ok=True)
    (root / "ontology" / "demo.ttl").write_text(TURTLE, encoding="utf-8")
    return ConnectorContent(CONNECTOR, root, "1.0.0")


def _installed(_distribution: str) -> ReleaseEvidence:
    return EVIDENCE


def _not_installed(_distribution: str) -> ReleaseEvidence:
    raise ReleaseDigestError("the distribution is not installed")


def server(content: ConnectorContent, *, installed: bool = True) -> FastMCP[object]:
    """The connector over MCP; ``installed=False`` is a source checkout with no wheel."""
    mcp: FastMCP[object] = FastMCP(content.connector, version=content.package_version)
    register_connector_content(
        mcp, content, release_evidence=_installed if installed else _not_installed
    )
    return mcp


@asynccontextmanager
async def session(
    content: ConnectorContent, *, installed: bool = True
) -> AsyncIterator[McpSession]:
    endpoint = TransportEndpoint(in_process=server(content, installed=installed))
    async with McpTransport().session(endpoint) as opened:
        yield opened
