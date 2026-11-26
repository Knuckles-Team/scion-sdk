"""Operation descriptors and the scope registry travel in the handshake (R045.12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

import scion_sdk.mcp.descriptors as descriptors_module
from scion_sdk.engine_capability import (
    EngineCapabilityUnavailable,
    engine_capability_present,
)
from scion_sdk.manifest.resource_descriptor import RESOURCES_URI
from scion_sdk.mcp.declaration_resource import register_live_declaration
from scion_sdk.mcp.descriptors import (
    OPERATION_META_KEY,
    OperationDescriptor,
    ScopeRegistryEntry,
    descriptor_from_engine_row,
    fetch_engine_contract,
    register_operation_descriptors,
    scopes_body,
)

READ = OperationDescriptor(
    scope="kg:read",
    stability="stable",
    idempotency="natural",
    replay_class="pure",
    audited=False,
    mutates=False,
)
WRITE = OperationDescriptor(
    scope="kg:write",
    stability="internal",
    idempotency="key-required",
    replay_class="journaled",
    audited=True,
    mutates=True,
)
SCOPES = [
    ScopeRegistryEntry(
        scope="kg:write", **{"class": "domain"}, owner="ingest", approver_group="ops"
    ),
    ScopeRegistryEntry(scope="kg:read", **{"class": "user"}, owner="graph"),
]


def _server() -> FastMCP:
    server = FastMCP("probe")

    @server.tool
    def lookup() -> str:
        """Look one record up."""
        return "ok"

    @server.tool
    def store() -> str:
        """Store one record."""
        return "ok"

    return server


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R045.12")
async def test_tools_list_carries_descriptor_and_scopes() -> None:
    server = _server()
    register_operation_descriptors(server, {"lookup": READ, "store": WRITE}, SCOPES)
    register_live_declaration(server, "probe")
    assert scopes_body(_server()) is None
    assert [s["scope"] for s in scopes_body(server) or []] == ["kg:read", "kg:write"]
    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["lookup"].meta is not None
        assert tools["lookup"].meta[OPERATION_META_KEY] == READ.model_dump()
        assert tools["store"].meta[OPERATION_META_KEY] == WRITE.model_dump()
        assert tools["store"].description == "Store one record."
        body = json.loads((await client.read_resource(RESOURCES_URI))[0].text)
    assert [s["scope"] for s in body["scopes"]] == ["kg:read", "kg:write"]
    assert body["scopes"][0] == {
        "scope": "kg:read",
        "class": "user",
        "owner": "graph",
        "approver_group": None,
    }
    # Refusals: a served tool without a descriptor, an unknown tool, an unlisted scope.
    for given, scopes in (
        ({"lookup": READ}, SCOPES),
        ({"lookup": READ, "store": WRITE, "ghost": READ}, SCOPES),
        ({"lookup": READ, "store": WRITE}, SCOPES[:1]),
        ({"lookup": READ, "store": WRITE}, [*SCOPES, SCOPES[0]]),
    ):
        with pytest.raises(ValueError):
            register_operation_descriptors(_server(), given, scopes)
    with pytest.raises(ValueError):
        OperationDescriptor.model_validate({**READ.model_dump(), "stability": "beta"})


def _row(**policy: object) -> dict[str, object]:
    base = {
        "authz_action": "kg:write",
        "mutates": True,
        "idempotent": False,
        "audited": True,
    }
    return {
        "id": "graph.Write",
        "stability": "stable",
        "replay_class": "journaled",
        "policy": {**base, **policy},
    }


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R045.12")
def test_engine_descriptor_maps_to_same_model() -> None:
    mapped = descriptor_from_engine_row(_row())
    assert mapped == OperationDescriptor(
        scope="kg:write",
        stability="stable",
        idempotency="key-required",
        replay_class="journaled",
        audited=True,
        mutates=True,
    )
    assert descriptor_from_engine_row(_row(idempotent=True)).idempotency == "natural"
    read = descriptor_from_engine_row(_row(mutates=False, audited=False))
    assert (read.idempotency, read.mutates) == ("none", False)


class _EngineDouble:
    """Structural double of the engine client's ``describe_contract`` read."""

    def __init__(self, digests: list[str]) -> None:
        self.calls: list[str | None] = []
        self._digests = digests

    async def describe_contract(self, cursor: str | None, limit: int) -> dict:
        assert limit == 500
        self.calls.append(cursor)
        digest = self._digests[min(len(self.calls), len(self._digests)) - 1]
        first = cursor is None
        scope = {"scope": "kg:write", "class": "domain", "owner": "ingest"}
        return {
            "methods": [{**_row(), "id": "graph.Write" if first else "graph.Write2"}],
            "scopes": [scope] if first else [],
            "schema_digest": digest,
            "next_cursor": "graph.Write" if first else None,
        }


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R045.12")
async def test_engine_contract_is_refused_while_the_pin_lacks_describe_contract() -> (
    None
):
    if engine_capability_present("DescribeContract"):
        pytest.skip("the pin serves DescribeContract")
    with pytest.raises(EngineCapabilityUnavailable) as caught:
        await fetch_engine_contract(_EngineDouble(["d"]))
    assert caught.value.method == "DescribeContract"


async def test_engine_contract_pages_through_a_structural_double(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        descriptors_module, "require_engine_capability", lambda _m: None
    )
    double = _EngineDouble(["d", "d"])
    methods, scopes = await fetch_engine_contract(double)
    assert sorted(methods) == ["graph.Write", "graph.Write2"]
    assert [s.scope for s in scopes] == ["kg:write"]
    assert double.calls == [None, "graph.Write"]
    moved = _EngineDouble(["a", "b", "b", "b"])
    methods, _ = await fetch_engine_contract(moved)  # digest changed: restarted once
    assert moved.calls[:3] == [None, "graph.Write", None]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R045.12")
def test_no_methods_json_or_scopes_json_read() -> None:
    source = Path(descriptors_module.__file__).read_text(encoding="utf-8")
    for forbidden in (".json", "read_text(", "open(", "importlib.resources"):
        assert forbidden not in source
