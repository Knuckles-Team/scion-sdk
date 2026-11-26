"""Caller-scoped tool context, ordered progress and receipts on served calls (SCION-CONNECTOR-CONTROL-R050)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp.types import ToolAnnotations

from scion_sdk.identity import ActorContext, ActorType, use_actor
from scion_sdk.mcp.call_context import (
    APPROVAL_META_KEY,
    RECEIPT_META_KEY,
    REFUSAL_CODES,
    ToolContextMiddleware,
    ToolRefusal,
    current_tool_context,
    current_tool_progress,
    record_effect,
)
from scion_sdk.mcp.tool_surface import register_tool_surface

ALICE = ActorContext("alice", ActorType.HUMAN, tenant_id="t1", authenticated=True)
BOB = ActorContext("bob", ActorType.HUMAN, tenant_id="t2", authenticated=True)
SECRET_ARG = "arg-zz9-private"  # sanitizer:ignore - synthetic fixture value


class Backend:
    def __init__(self) -> None:
        self.effects = 0
        self.seen: list[Any] = []


class _As(Middleware):
    """Bind a fixed actor for the request, as the served identity path would."""

    def __init__(self, actor: ActorContext) -> None:
        self._actor = actor

    async def on_request(self, context: Any, call_next: Any) -> Any:
        with use_actor(self._actor):
            return await call_next(context)


def _server(backend: Backend, actor: ActorContext = ALICE) -> FastMCP[Any]:
    mcp: FastMCP[Any] = FastMCP("ctxsrv")
    mcp.add_middleware(_As(actor))
    mcp.add_middleware(ToolContextMiddleware("ctxsrv"))

    @mcp.tool
    async def work(note: str = "") -> str:
        context = current_tool_context()
        backend.seen.append(context)
        progress = current_tool_progress()
        assert progress is not None
        await progress.update(1, total=3, message="started")
        await progress.update(2, total=3)
        backend.effects += 1
        record_effect("effect:1")
        return "done"

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    async def wipe() -> str:
        backend.effects += 1
        return "wiped"

    @mcp.tool
    async def explode() -> str:
        record_effect("effect:partial")
        raise RuntimeError(SECRET_ARG)

    return mcp


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R050")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R050.6")
async def test_served_context_progress_and_approval() -> None:
    backend = Backend()
    seen: list[tuple[float, float | None]] = []

    async def on_progress(
        progress: float, total: float | None, message: str | None
    ) -> None:
        seen.append((progress, total))

    async with Client(_server(backend)) as client:
        result = await client.call_tool("work", {}, progress_handler=on_progress)
        meta = result.meta or {}
        again = await client.call_tool("work", {})
        no_token = (again.meta or {})["scion/receipt"]
    async with Client(_server(backend, BOB)) as other:
        await other.call_tool("work", {})
    async with Client(_server(backend)) as client:
        approved = await client.call_tool(
            "wipe", {}, meta={APPROVAL_META_KEY: "approval:1"}
        )
        with pytest.raises(MCPError):
            await client.call_tool("wipe", {})
    assert seen == [(1.0, 3.0), (2.0, 3.0)]  # ordered, before the terminal result
    receipt = meta[RECEIPT_META_KEY]
    assert receipt["last_sequence"] == 2 and receipt["state"] == "succeeded"
    assert receipt["effect_receipt_ref"] == "effect:1"
    assert (
        no_token["last_sequence"] == 2
        and no_token["request_id"] != receipt["request_id"]
    )
    assert [c.tenant_id for c in backend.seen] == ["t1", "t1", "t2"]
    assert approved.data == "wiped" and backend.effects == 4  # 3 work + 1 approved wipe


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R050.3")
async def test_both_registrars_share_context_identity() -> None:
    seen: list[Any] = []

    class Api:
        def read_item(self) -> dict[str, Any]:
            """Read one item."""
            seen.append(current_tool_context())
            return {"ok": True}

    def register_extra_tools(server: FastMCP[Any]) -> None:
        @server.tool
        def extra_item() -> str:
            seen.append(current_tool_context())
            return "extra"

    mcp: FastMCP[Any] = FastMCP("both")
    mcp.add_middleware(_As(ALICE))
    mcp.add_middleware(ToolContextMiddleware("both"))
    register_tool_surface(
        mcp,
        service="both",
        client_cls=Api,
        get_client=Api,
        registrars=[register_extra_tools],
    )
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
        await client.call_tool("both_read_item", {})
        await client.call_tool("extra_item", {})
    assert {"both_read_item", "extra_item"} <= names
    first, second = seen
    assert (first.connector_id, first.tenant_id) == (
        second.connector_id,
        second.tenant_id,
    )
    assert (
        first.operation_id == "both_read_item" and second.operation_id == "extra_item"
    )
    assert first.request_id != second.request_id


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R050.4")
async def test_refusals_make_zero_backend_calls() -> None:
    backend = Backend()
    async with Client(_server(backend)) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("wipe", {})
        assert "approval_required" in str(caught.value) and backend.effects == 0
    blank = ActorContext("  ", ActorType.HUMAN, tenant_id="t1")
    async with Client(_server(backend, blank)) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("work", {})
        assert "invalid_context" in str(caught.value) and backend.effects == 0
    assert {
        "approval_required",
        "invalid_context",
        "recovery_unavailable",
    } <= REFUSAL_CODES
    with pytest.raises(ValueError):
        ToolRefusal("made_up")
    error = ToolRefusal("cancelled").to_error()
    assert isinstance(error, MCPError) and error.error.code == -32000

    # a repeated request id refuses recovery_unavailable and runs no backend call
    mcp = _server(backend)
    middleware = next(m for m in mcp.middleware if isinstance(m, ToolContextMiddleware))
    middleware._remember("req-1", "succeeded")
    assert middleware._ledger["req-1"] == "succeeded"
    for index in range(1100):
        middleware._remember(f"r{index}", "succeeded")
    assert len(middleware._ledger) == 1024 and "req-1" not in middleware._ledger


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R050.5")
async def test_receipt_correlates_and_redacts() -> None:
    backend = Backend()
    async with Client(_server(backend)) as client:
        ok = await client.call_tool("work", {"note": SECRET_ARG})
        with pytest.raises(ToolError):
            await client.call_tool("explode", {})
    receipt = (ok.meta or {})["scion/receipt"]
    assert receipt["tenant"] == "t1" and receipt["connector"] == "ctxsrv"
    assert (
        receipt["operation"] == "work" and receipt["trace_id"] == receipt["request_id"]
    )
    assert (
        len(receipt["schema_digest"]) == 64 and receipt["counters"]["cache_hits"] == 0
    )
    assert SECRET_ARG not in json.dumps(receipt)
