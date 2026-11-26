"""A served connector allocates once and reads back (R051.3, R051.6, root R051)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from sandbox_support import DisposableBackend

from scion_sdk.adapters.sandbox_provisioner import GuardedSandboxProvisioner
from scion_sdk.mcp.sandbox_tools import register_sandbox_tools
from scion_sdk.ports.sandbox import SandboxMatrix
from scion_sdk.testing.sandbox_conformance import conformance_request


def _served(backend: DisposableBackend) -> FastMCP[object]:
    mcp: FastMCP[object] = FastMCP("container-connector")
    provider = GuardedSandboxProvisioner(
        backend,
        SandboxMatrix(revision="m1", profiles=frozenset({"small"})),
        adapter_version="1",
    )
    assert register_sandbox_tools(mcp, provider) == (
        "sandbox_provision",
        "sandbox_reconcile",
        "sandbox_release",
    )
    return mcp


def _wire(profile: str = "small") -> dict[str, Any]:
    return json.loads(conformance_request(profile).model_dump_json())


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051.3")
async def test_registered_tool_reaches_provisioner() -> None:
    backend = DisposableBackend()
    async with Client(_served(backend)) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert {"sandbox_provision", "sandbox_reconcile"} <= names
        reply = await client.call_tool("sandbox_provision", {"request": _wire()})
    assert reply.data["allocation_id"] == "alloc-1" and backend.allocations == 1


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051.6")
async def test_agents_run_allocates_once_and_reads_back() -> None:
    backend = DisposableBackend()
    backend.lose_response = True
    wire = _wire()
    async with Client(_served(backend)) as client:
        first = await client.call_tool("sandbox_provision", {"request": wire})
        again = await client.call_tool("sandbox_provision", {"request": wire})
        read = await client.call_tool(
            "sandbox_reconcile", {"operation_id": "op-conformance"}
        )
        with pytest.raises(ToolError, match="unsupported_profile"):
            await client.call_tool("sandbox_provision", {"request": _wire("huge")})
    assert first.data == again.data == read.data
    assert backend.allocations == 1  # response loss and replay never duplicate
