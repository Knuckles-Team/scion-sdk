"""A tiny connector package served in process with optional signed release evidence."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from release_support import release_evidence

from scion_sdk.artifacts.prompts import PromptArtifactKind
from scion_sdk.artifacts.resources import ResourceArtifactKind
from scion_sdk.artifacts.skills import SkillArtifactKind
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


def server(content: ConnectorContent) -> FastMCP[object]:
    mcp: FastMCP[object] = FastMCP(content.connector, version=content.package_version)
    register_connector_content(mcp, content)
    return mcp


@asynccontextmanager
async def session(content: ConnectorContent) -> AsyncIterator[McpSession]:
    endpoint = TransportEndpoint(in_process=server(content))
    async with McpTransport().session(endpoint) as opened:
        yield opened


async def sign_package(content: ConnectorContent) -> None:
    """Write ``release.json`` signed over the package's live inputs."""
    async with session(content) as opened:
        evidence = await release_evidence(opened, content.connector)
    content.release_path.write_text(json.dumps(evidence), encoding="utf-8")
