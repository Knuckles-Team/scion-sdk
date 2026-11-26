"""Typed transport declarations and the live access-contract parser."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import cast

import pytest

from scion_sdk.access_contract import (
    AccessContractError,
    TransportContract,
    parse_access_contracts,
)
from scion_sdk.contract_fields import AUTH_MODELS, STREAM_MODES


def _contract(kind: str, pushdown: str = '"time_range"') -> str:
    return f'''@prefix ac: <https://knuckles-team.github.io/ontology/access#> .
@prefix ex: <https://example.invalid/> .
ex:feed a ac:AccessContract ; ac:classIri ex:Event ; ac:sourceId "source" ;
 ac:entity "event" ; ac:keyField "id" ; ac:accessKind "{kind}" ;
 ac:operation "read" ; ac:authModel "sasl" ; ac:streaming "offset" ;
 ac:pushdown {pushdown} .'''


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069.1")
def test_transport_contract_round_trips() -> None:
    assert "sasl" in AUTH_MODELS
    assert "offset" in STREAM_MODES
    mutable = {"time_range"}
    value = TransportContract(
        "stream_topic",
        "sasl",
        streaming="offset",
        pushdown=cast(frozenset[str], mutable),
    )
    mutable.add("filter")
    assert value.pushdown == frozenset({"time_range"})
    assert TransportContract(**asdict(value)) == value
    assert TransportContract.from_json(json.loads(json.dumps(value.to_json()))) == value
    with pytest.raises(ValueError, match="duplicate"):
        TransportContract.from_json(
            value.to_json() | {"pushdown": ["time_range", "time_range"]}
        )
    with pytest.raises(ValueError, match="protocol"):
        TransportContract("unknown", "none")
    with pytest.raises(ValueError, match="pushdown"):
        TransportContract("http_endpoint", "none", pushdown=frozenset({"unknown"}))


def test_turtle_reads_stream_contract_and_refuses_unsupported_pushdown() -> None:
    (value,) = parse_access_contracts(_contract("stream_topic"))
    assert value.transport == TransportContract(
        "stream_topic", "sasl", streaming="offset", pushdown=frozenset({"time_range"})
    )
    with pytest.raises(AccessContractError, match="invalid transport declaration"):
        parse_access_contracts(_contract("stream_topic", '"filter"'))


def test_stream_transport_requires_auth_model() -> None:
    with pytest.raises(AccessContractError, match="missing ac:authModel"):
        parse_access_contracts(
            _contract("stream_topic").replace(' ac:authModel "sasl" ;', "")
        )


@pytest.mark.parametrize(
    "protocol",
    [
        "http_endpoint",
        "graphql_query",
        "grpc_method",
        "odata_entity_set",
        "soap_operation",
        "jsonrpc_method",
        "mcp_tool",
        "a2a_skill",
        "sparql_endpoint",
        "graph_endpoint",
        "virtual_graph",
        "stream_topic",
        "change_feed",
        "webhook_event",
        "event_stream",
        "file_store",
        "directory",
        "mailbox",
        "calendar_collection",
    ],
)
def test_declared_protocols_round_trip(protocol: str) -> None:
    contract = TransportContract(protocol, "none")
    assert TransportContract(**asdict(contract)) == contract


def _served_server(descriptors, contracts_ttl: str = ""):  # type: ignore[no-untyped-def]
    from fastmcp import FastMCP
    from fastmcp.resources import TextResource
    from pydantic import AnyUrl

    from scion_sdk.ingest.live_declaration import source_meta
    from scion_sdk.mcp.declaration_resource import register_live_declaration

    server = FastMCP("demo-mcp")

    @server.tool(
        meta=source_meta(
            source_id="demo",
            record_type="Item",
            id_field="id",
            records_path="items",
            preset={
                "pagination": "cursor",
                "cursor_param": "cursor",
                "cursor_path": "cursor",
                "params_style": "args",
            },
        )
    )
    def read_items(cursor: str = ""):
        return {"items": [], "cursor": cursor}

    register_live_declaration(server, "demo-mcp", lambda: descriptors)
    if contracts_ttl:
        uri = "ontology://demo-mcp/contracts.ttl"
        server.add_resource(
            TextResource(
                uri=AnyUrl(uri), name=uri, text=contracts_ttl, mime_type="text/turtle"
            )
        )
    return server


async def _certify(server):  # type: ignore[no-untyped-def]
    from scion_sdk.certify.certification import certify_connector
    from scion_sdk.ports.session import TransportEndpoint
    from scion_sdk.transports.mcp import McpTransport

    return await certify_connector(
        McpTransport(), TransportEndpoint(in_process=server), timeout_seconds=10
    )


def _descriptor(name: str, access_kind: str) -> dict[str, object]:
    return {
        "name": name,
        "uri": f"data://demo-mcp/{name}",
        "kind": "data",
        "origin": "declared",
        "ontology_class": None,
        "mapping_status": "unmapped",
        "fields": [],
        "access_kind": access_kind,
        "operation": "read_items",
        "pagination": "cursor",
    }


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069.2")
def test_registry_one_transport_per_protocol() -> None:
    import importlib

    from scion_sdk.manifest import resource_descriptor_model
    from scion_sdk.transports.registry import registered_transport, transport_matrix

    assert [
        r["protocol"]
        for r in transport_matrix(["mcp_tool", "stream_topic", "a2a_skill"])
    ] == ["a2a_skill", "mcp_tool"]
    expected = {
        "http_endpoint": (
            "scion_sdk.http.client",
            "api_key",
            "cursor",
            {"filter", "project", "sort", "limit"},
        ),
        "graphql_query": (
            "scion_sdk.transports.graphql",
            "bearer",
            "cursor",
            {"filter", "project", "limit"},
        ),
        "mcp_tool": (
            "scion_sdk.transports.mcp",
            "bearer",
            "cursor",
            {"filter", "limit"},
        ),
        "a2a_skill": ("scion_sdk.a2a.client", "bearer", "none", {"filter"}),
        "virtual_graph": (
            "scion_sdk.source_connectors.universal",
            "password",
            "cursor",
            {"filter", "project", "join", "aggregate", "limit", "time_range"},
        ),
    }
    for protocol, (module, auth, paging, pushdown) in expected.items():
        registration = registered_transport(protocol)
        assert registration is not None
        assert registration.module == module
        importlib.import_module(module)
        assert registration.contract.auth_model == auth
        assert registration.contract.paging == paging
        assert registration.contract.pushdown == pushdown
    assert registered_transport("stream_topic") is None
    assert not hasattr(resource_descriptor_model, "_RESOURCE_ACCESS_KINDS")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069.3")
async def test_certification_checks_registry() -> None:
    clean = await _certify(_served_server([_descriptor("items", "http_endpoint")]))
    assert clean.transport_defects == ()
    assert clean.passed
    bad = await _certify(_served_server([_descriptor("feed", "stream_topic")]))
    assert any(d.startswith("TRANSPORT_UNIMPLEMENTED") for d in bad.transport_defects)
    assert not bad.passed


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069.4")
async def test_transport_refusals() -> None:
    from scion_sdk.transports import registry
    from scion_sdk.transports.registry import (
        TransportRegistration,
        TransportRegistryError,
        check_declared_transports,
        register_transport,
        registered_transport,
    )

    before = dict(registry._REGISTRY)
    existing = registered_transport("mcp_tool")
    assert existing is not None
    with pytest.raises(TransportRegistryError) as duplicate:
        register_transport(
            TransportRegistration(TransportContract("mcp_tool", "none"), "x.y")
        )
    assert duplicate.value.code == "DUPLICATE_TRANSPORT"
    assert before == registry._REGISTRY
    (contract,) = parse_access_contracts(
        _contract("mcp_tool", '"filter", "join"').replace(
            'ac:authModel "sasl" ; ac:streaming "offset" ;', ""
        )
    )
    assert check_declared_transports([], [contract])[0].startswith(
        "PUSHDOWN_UNSUPPORTED"
    )
    unregistered = {"name": "n", "access_kind": "grpc_method"}
    assert check_declared_transports([unregistered], [])[0].startswith(
        "TRANSPORT_UNIMPLEMENTED"
    )
    unknown = {"name": "n", "access_kind": "carrier_pigeon"}
    assert check_declared_transports([unknown], [])[0].startswith("ACCESS_KIND_UNKNOWN")
    assert before == registry._REGISTRY


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R069.5")
async def test_certification_records_matrix() -> None:
    descriptors = [
        _descriptor("a", "http_endpoint"),
        _descriptor("b", "graphql_query"),
        _descriptor("c", "mcp_tool"),
    ]
    report = await _certify(_served_server(descriptors))
    matrix = report.document()["transports"]
    assert [row["protocol"] for row in matrix] == [
        "graphql_query",
        "http_endpoint",
        "mcp_tool",
    ]
    http = matrix[1]
    assert http["module"] == "scion_sdk.http.client"
    assert http["pushdown"] == ["filter", "limit", "project", "sort"]
    assert set(http) == {
        "protocol",
        "module",
        "auth_model",
        "paging",
        "streaming",
        "pushdown",
    }
