"""R054.3-.6: source and replacement servers driven over tools/list and tools/call."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from scion_sdk.mcp.server import create_mcp_server
from scion_sdk.mcp.tool_surface import register_tool_surface
from scion_sdk.testing.fleet_migration import (
    JsonTagLoaderEntry,
    LoaderClassification,
    OperationMapping,
    ParityCall,
    ParityProblem,
    ParityReceipt,
    ServerUnderParity,
    run_loader_parity,
)


class Backend:
    """The controlled backend each connector fixture supplies."""

    def __init__(self) -> None:
        self.calls = 0

    def get_item(self, item_id: str) -> dict[str, str]:
        """Read one item."""
        if not item_id:
            raise ToolError("denied: item_id required")
        self.calls += 1
        return {"id": item_id}


def _source(
    backend: Backend,
    *,
    tags: set[str] = frozenset({"backend", "gated", "operation"}),  # type: ignore[assignment]
) -> FastMCP[Any]:
    mcp: FastMCP[Any] = FastMCP("old")

    @mcp.tool(name="svc_get_item", tags=set(tags))
    def get_item(item_id: str) -> dict[str, str]:
        """Read one item."""
        return backend.get_item(item_id)

    return mcp


def _replacement(backend: Backend, kind: str) -> FastMCP[Any]:
    _a, mcp, _m = create_mcp_server(
        "new", version="0.0.0", command_args=["--transport", "stdio"]
    )
    if kind == "explicit_callback":

        def extra(server: Any) -> None:
            @server.tool(name="svc_get_item", tags={"backend", "gated", "operation"})
            def get_item(item_id: str) -> dict[str, str]:
                """Read one item."""
                if not item_id:
                    raise ToolError("denied: item_id required")
                return backend.get_item(item_id)

        register_tool_surface(mcp, service="svc", verbose_register=extra)
    else:
        register_tool_surface(
            mcp,
            service="svc",
            client_cls=Backend,
            get_client=lambda: backend,
            action_dispatch=kind == "action_dispatch",
        )
    return mcp


def _entries(
    kind: str, *, target: str = "svc_get_item", tags=("backend", "gated", "operation")
):
    mapping = OperationMapping(
        "svc_get_item",
        target,
        "i",
        "i",
        "o",
        "o",
        frozenset(tags),
        frozenset(tags),
        "read",
        "read",
        kind,
    )
    return [
        JsonTagLoaderEntry(
            "pkg",
            "rev",
            "pkg/load.py",
            "pkg/tags.json",
            "d" * 64,
            kind,
            (mapping,),
            "org/pkg",
            "pkg.tools",
        )
    ]


async def _run(
    kind: str, calls, *, with_source: bool = True, entries=None, source_tags=None
):
    old, new = Backend(), Backend()
    source = _source(old) if source_tags is None else _source(old, tags=source_tags)
    return await run_loader_parity(
        entries if entries is not None else _entries(kind),
        ServerUnderParity(source, lambda: old.calls) if with_source else None,
        ServerUnderParity(_replacement(new, kind), lambda: new.calls),
        calls,
        sdk_revision="r1",
        protocol_version="2025-06",
        fixture_seed="seed",
    )


PERMIT = ParityCall("svc_get_item", {"item_id": "7"}, True)
DENY = ParityCall("svc_get_item", {"item_id": ""}, False)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054.3")
async def test_tools_list_and_call_reach_backend() -> None:
    receipt = await _run("operation", [PERMIT])
    assert isinstance(receipt, ParityReceipt)
    assert receipt.backend_counts == (("svc_get_item", 1, 1),)
    assert ("svc_get_item", "input_schema", "equal") in receipt.comparisons
    assert [p for p in receipt.problems if p.code != "schema_difference"] == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054.4")
async def test_denied_malformed_and_unknown_calls_have_zero_effects() -> None:
    receipt = await _run("operation", [DENY])
    assert receipt.backend_counts == (("svc_get_item", 0, 0),)
    assert not [
        p for p in receipt.problems if p.code in {"backend_calls", "refusal_difference"}
    ]
    malformed = ParityCall("svc_get_item", {"wrong": 1}, False)
    unknown = ParityCall("svc_unknown", {}, False)
    receipt = await _run("operation", [malformed, unknown])
    assert all(isinstance(p, ParityProblem) for p in receipt.problems)
    assert ("unknown_loader", "svc_unknown") in {
        (p.code, p.subject) for p in receipt.problems
    }
    assert receipt.backend_counts == (("svc_get_item", 0, 0),)
    missing = await _run("operation", [PERMIT], with_source=False)
    assert [p.code for p in missing.problems] == ["missing_baseline"]
    assert missing.comparisons == ()
    mismatch = await _run(
        "operation", [], entries=_entries("operation", target="svc_absent")
    )
    assert "operation_mismatch" in {p.code for p in mismatch.problems}
    drift = await _run("operation", [], source_tags={"x"})
    assert "schema_difference" in {p.code for p in drift.problems}
    unknown_inventory = await run_loader_parity(
        [],
        None,
        ServerUnderParity(FastMCP("n"), lambda: 0),
        [],
        sdk_revision="r",
        protocol_version="p",
        fixture_seed="s",
        inventory=[("pkg", "pkg/hidden.py")],
    )
    assert "unknown_loader" in {p.code for p in unknown_inventory.problems}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054.5")
async def test_parity_receipt_reconstructs_from_digests() -> None:
    first = await _run("operation", [PERMIT])
    second = await _run("operation", [PERMIT])
    assert first.digest() == second.digest()
    body = json.loads(first.canonical_json())
    assert (
        body["inventory_digest"] == first.inventory_digest and len(first.digest()) == 64
    )
    assert body["fixture_seed"] == "seed" and body["protocol_version"] == "2025-06"
    assert first.canonical_json() == json.dumps(
        body, sort_keys=True, separators=(",", ":")
    )
    different = await _run("operation", [PERMIT, PERMIT])
    assert different.digest() != first.digest()


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054.6")
async def test_default_and_opt_ins_preserve_dispatch_parity() -> None:
    for kind in ("operation", "explicit_callback", "action_dispatch"):
        receipt = await _run(kind, [PERMIT, DENY])
        codes = {p.code for p in receipt.problems}
        assert not codes - {"schema_difference"}, (kind, receipt.problems)
        assert receipt.backend_counts == (
            ("svc_get_item", 1, 1),
            ("svc_get_item", 0, 0),
        )


def test_classification_is_a_value_record() -> None:
    item = LoaderClassification("pkg", "a.py", "t.json", "migrated", "parity holds")
    assert item.status == "migrated"
