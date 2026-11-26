"""A served two-step run reuses an observation and refuses stale ones (R052.3, .5, .6)."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from observation_support import NOW, Store, context, envelope

from scion_sdk.mcp.action_dispatch import dispatch_async
from scion_sdk.mcp.tool_context import (
    OBSERVATION_META_KEY,
    ObservationRefused,
    ObservedResult,
    resolve_observation,
)


class _Desktop:
    """A connector client whose ``capture`` method returns an observation."""

    def __init__(self) -> None:
        self.captures = 0
        self.reads = 0

    async def capture(self) -> ObservedResult:
        self.captures += 1
        return ObservedResult({"captured": True}, envelope())

    async def read_frame(self) -> dict[str, Any]:
        self.reads += 1
        return {"frame": "read"}


def _server(desktop: _Desktop, store: Store, run: dict[str, str]) -> FastMCP[object]:
    mcp: FastMCP[object] = FastMCP("desktop")

    @mcp.tool
    async def capture() -> ToolResult:
        reply = await dispatch_async(
            desktop, "capture", execution=context(session_id=run["id"])
        )
        return ToolResult(structured_content=reply, meta=reply["_meta"])

    @mcp.tool
    async def next_step(observation_id: str) -> dict[str, Any]:
        try:
            _, evidence = resolve_observation(
                context(session_id=run["id"], operation_id="read-frame"),
                observation_id,
                store,
                clock=lambda: NOW,
            )
        except ObservationRefused as refusal:
            raise ToolError(refusal.code) from None
        await desktop.read_frame()
        return evidence.model_dump(mode="json")

    return mcp


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052.3")
async def test_envelope_reaches_arbora_state_and_consumer() -> None:
    desktop, store, run = _Desktop(), Store(), {"id": "run-1"}
    async with Client(_server(desktop, store, run)) as client:
        reply = await client.call_tool("capture", {})
    meta = reply.meta or {}
    assert meta[OBSERVATION_META_KEY]["observation_id"] == "obs-1"
    assert desktop.captures == 1


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052.5")
async def test_readback_returns_provenance_without_content() -> None:
    desktop, store, run = _Desktop(), Store(), {"id": "run-1"}
    store.commit(envelope())
    async with Client(_server(desktop, store, run)) as client:
        reply = await client.call_tool("next_step", {"observation_id": "obs-1"})
    assert reply.data["content_digest"] == "c" * 64
    assert "content_ref" not in reply.data and "blob://" not in str(reply.data)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052.6")
async def test_two_step_run_reuses_observation_and_refuses_stale() -> None:
    desktop, store, run = _Desktop(), Store(), {"id": "run-1"}
    async with Client(_server(desktop, store, run)) as client:
        await client.call_tool("capture", {})
        store.commit(envelope())  # the host commits the reference it received
        reused = await client.call_tool("next_step", {"observation_id": "obs-1"})
        assert reused.data["run_id"] == "run-1" and desktop.reads == 1
        run["id"] = "run-2"  # another run never sees it
        with pytest.raises(ToolError, match="observation_uncommitted"):
            await client.call_tool("next_step", {"observation_id": "obs-1"})
        run["id"] = "run-1"
        store.commit(envelope(), state="invalidated")
        with pytest.raises(ToolError, match="observation_invalidated"):
            await client.call_tool("next_step", {"observation_id": "obs-1"})
    assert (
        desktop.reads == 1 and desktop.captures == 1
    )  # refusals cost no provider call
