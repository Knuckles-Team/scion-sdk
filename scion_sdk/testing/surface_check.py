"""Connector surface self-check (SCION-CONNECTOR-CONTROL-R916.4, R916.5).

:func:`surface_check` builds the connector server in process, lists its tools,
prompts and resources over an MCP client, and returns the
:func:`~scion_sdk.surface_contracts.surface_findings` of that served surface
against the package's ``connector_contracts`` declaration.

Run ``python -m scion_sdk.testing.surface_check <package>``; it exits 1 when
any finding remains.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from scion_sdk.connector import build_server
from scion_sdk.mcp.content import PROMPT_META_KEY
from scion_sdk.surface_contracts import load_surface, surface_findings

__all__ = ["ServedSurface", "main", "served_surface", "surface_check"]


@dataclass(frozen=True)
class ServedSurface:
    """What one server lists over MCP."""

    tools: set[str]
    tool_schemas: dict[str, dict[str, Any]]
    prompts: set[str]
    skills: set[str]
    resources: set[str]
    prompt_documents: dict[str, dict[str, Any]]


def _prompt_document(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Rebuild the identity and agent of a prompt from its served ``_meta``."""
    entry = (meta or {}).get(PROMPT_META_KEY) or {}
    return {
        "identity": {"role": entry.get("role"), "goal": entry.get("goal")},
        "agent": entry.get("agent"),
    }


def served_surface(mcp: Any) -> ServedSurface:
    """List the tools, prompts, skills and resources ``mcp`` serves."""
    from fastmcp import Client

    async def read() -> ServedSurface:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            resources = {str(r.uri) for r in await client.list_resources()}
            listed = await client.list_prompts()
        prompts = {p.name for p in listed}
        documents = {p.name: _prompt_document(p.meta) for p in listed}
        skills = {
            uri.removeprefix("skill://").split("/", 1)[0]
            for uri in resources
            if uri.startswith("skill://")
        }
        return ServedSurface(
            tools={t.name for t in tools},
            tool_schemas={t.name: dict(t.input_schema or {}) for t in tools},
            prompts=prompts,
            skills=skills,
            resources=resources,
            prompt_documents=documents,
        )

    return asyncio.run(read())


def surface_check(package: str) -> list[str]:
    """Return the sorted surface findings of the connector ``package``."""
    mcp = build_server(package, command_args=[])[1]
    surface = served_surface(mcp)
    return surface_findings(
        load_surface(package, connector=mcp.name),
        tools=surface.tools,
        prompts=surface.prompts,
        skills=surface.skills,
        resources=surface.resources,
        tool_schemas=surface.tool_schemas,
        prompt_documents=surface.prompt_documents,
    )


def main(argv: list[str] | None = None) -> int:
    """Print each finding of the named package; exit 1 when any remains."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: python -m scion_sdk.testing.surface_check <package>\n")
        return 2
    findings = surface_check(args[0])
    for finding in findings:
        sys.stderr.write(f"{finding}\n")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
