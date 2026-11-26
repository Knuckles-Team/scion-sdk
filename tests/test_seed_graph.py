"""The connector seed graph model, emission and refusals (R068)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from scion_sdk.manifest.seed_graph import (
    MAX_SEED_BYTES,
    MAX_SEED_TRIPLES,
    SEED_PARTS,
    SeedGraphDeclaration,
    SeedGraphError,
    SeedTriple,
    SeedTurtle,
    emit_seed_turtle,
)
from scion_sdk.mcp.content import SEED_RESOURCE_URI

NS = "https://example.test/"


def _declaration() -> SeedGraphDeclaration:
    return SeedGraphDeclaration(
        instance_iri=NS + "instance/1",
        identity=(SeedTriple(NS + "instance/1", NS + "name", "demo", literal=True),),
        catalog=(
            SeedTriple(NS + "instance/1", NS + "hasResource", NS + "res/b"),
            SeedTriple(NS + "instance/1", NS + "hasResource", NS + "res/a"),
        ),
        references=(SeedTriple(NS + "res/a", NS + "refersTo", NS + "res/b"),),
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.1")
def test_seed_declaration_round_trips() -> None:
    declaration = _declaration()
    assert declaration.instance_iri.endswith("/1")
    assert SEED_RESOURCE_URI.format(connector="demo") == "seed://demo/seed.ttl"
    with pytest.raises(SeedGraphError) as blank:
        SeedGraphDeclaration(instance_iri="  ")
    assert blank.value.code == "seed_instance_iri_blank"
    with pytest.raises(AttributeError):
        declaration.instance_iri = "x"  # type: ignore[misc]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.2")
def test_seed_turtle_is_deterministic() -> None:
    first, second = emit_seed_turtle(_declaration()), emit_seed_turtle(_declaration())
    assert isinstance(first, SeedTurtle)
    assert first.turtle == second.turtle and first.digest == second.digest
    assert tuple(first.digests) == SEED_PARTS
    assert all(len(d) == 64 for d in first.digests.values())
    catalog_lines = first.parts["catalog"].splitlines()
    assert catalog_lines == sorted(catalog_lines)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.4")
def test_seed_refusals() -> None:
    bulk = SeedGraphDeclaration(
        NS + "i",
        identity=(SeedTriple(NS + "i", NS + "rows", '[{"a": 1}]', literal=True),),
    )
    with pytest.raises(SeedGraphError) as caught:
        emit_seed_turtle(bulk)
    assert caught.value.code == "seed_bulk_data"
    many = SeedGraphDeclaration(
        NS + "i",
        catalog=tuple(
            SeedTriple(NS + "i", NS + "has", f"{NS}o/{n}")
            for n in range(MAX_SEED_TRIPLES + 1)
        ),
    )
    with pytest.raises(SeedGraphError) as large:
        emit_seed_turtle(many)
    assert large.value.code == "seed_too_large"
    assert MAX_SEED_BYTES == 8 * 1024 * 1024


def _served(tmp_path):  # type: ignore[no-untyped-def]
    from scion_sdk.mcp.content import ConnectorContent
    from scion_sdk.mcp.server import create_mcp_server

    ontology = tmp_path / "ontology"
    ontology.mkdir(exist_ok=True)
    (ontology / "widget.ttl").write_text(
        "@prefix : <https://knuckles-team.github.io/ontology/core#> .\n"
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
        "<http://example.invalid/widget> a owl:Ontology ; "
        "owl:imports <https://knuckles-team.github.io/ontology/core> .\n"
        ":Widget a owl:Class .\n:serialNumber a owl:DatatypeProperty .\n"
    )
    _, server, registration = create_mcp_server(
        "widget-mcp",
        version="1.0.0",
        command_args=[],
        content=ConnectorContent("widget-mcp", tmp_path, "1.0.0"),
    )
    return ontology, server, registration


class Widget(BaseModel):
    id: str
    serial_number: str


def _add_widget_tool(server) -> None:  # type: ignore[no-untyped-def]
    @server.tool(annotations={"readOnlyHint": True})
    def list_widgets(cursor: str | None = None) -> list[Widget]:
        return []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.3")
async def test_server_registers_seed_and_notifies(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import anyio
    from fastmcp import Client

    from scion_sdk.mcp.change_events import serve_change_subscriptions
    from scion_sdk.mcp.content import refresh_seed_resource
    from scion_sdk.ports.change_source import ChangeEvent, ChangeSource
    from scion_sdk.ports.session import TransportEndpoint
    from scion_sdk.transports.mcp import McpTransport

    _, server, _ = _served(tmp_path)
    serve_change_subscriptions(server)
    uri = "seed://widget-mcp/seed.ttl"
    async with Client(server) as client:
        before = (await client.read_resource(uri))[0].text
    assert "hasResource" not in before
    async with McpTransport().session(TransportEndpoint(in_process=server)) as session:
        assert isinstance(session, ChangeSource)
        assert await session.watch([uri])
        assert await refresh_seed_resource(server) is False  # no change, no event
        _add_widget_tool(server)
        assert await refresh_seed_resource(server) is True
        seen: set[ChangeEvent] = set()
        with anyio.fail_after(5):  # bound: the in-process bus delivers at once
            while ChangeEvent("resources") not in seen:
                seen |= await session.next_changes()
    async with Client(server) as client:
        after = (await client.read_resource(uri))[0].text
    assert "urn:scion:seed:hasResource" in after


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.5")
async def test_resource_metadata_carries_digest(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import hashlib

    from fastmcp import Client

    _, server, _ = _served(tmp_path)
    async with Client(server) as client:
        listed = [
            r for r in await client.list_resources() if str(r.uri).startswith("seed://")
        ]
        body = (await client.read_resource("seed://widget-mcp/seed.ttl"))[0].text
    (resource,) = listed
    meta = resource.meta or {}
    assert meta["scion/digest"] == hashlib.sha256(body.encode()).hexdigest()


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.3")
async def test_seed_building_blocks_compose(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastmcp import FastMCP

    from scion_sdk.mcp._content_descriptors import resource_descriptor_source
    from scion_sdk.mcp.change_events import (
        announce_resources_changed,
        serve_change_subscriptions,
    )
    from scion_sdk.mcp.content import (
        DIGEST_META_KEY,
        ConnectorContent,
        register_seed_resource,
        seed_declaration,
    )

    content = ConnectorContent("widget-mcp", tmp_path, "1.0.0")
    declaration = seed_declaration(
        content, [{"name": "widgets", "ontology_class": NS + "W"}]
    )
    assert declaration.instance_iri == "seed://widget-mcp/instance"
    assert len(declaration.catalog) == 1 and len(declaration.references) == 1
    mcp: FastMCP = FastMCP("seed")
    assert register_seed_resource(mcp, content, lambda: []) is True
    resource = await mcp.get_resource("seed://widget-mcp/seed.ttl")
    assert DIGEST_META_KEY in (resource.meta or {})
    assert resource_descriptor_source(mcp, content) is None  # no ontology, no sources
    serve_change_subscriptions(mcp)
    await announce_resources_changed(mcp)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R068.3")
async def test_the_seed_waits_for_a_declared_tool_then_is_served(tmp_path) -> None:
    from fastmcp import FastMCP

    from scion_sdk.mcp.content import (
        ConnectorContent,
        install_pending_seed,
        register_seed_resource,
    )

    registered: list[dict[str, str]] = []

    def descriptors() -> list[dict[str, str]]:
        if not registered:
            raise ValueError("connector_sources declares unknown tool 'demo_tool'")
        return registered

    content = ConnectorContent("late-mcp", tmp_path, "1.0.0")
    mcp: FastMCP = FastMCP("late")
    assert register_seed_resource(mcp, content, descriptors) is False
    assert install_pending_seed(mcp) is False  # the tool is still not registered
    registered.append({"name": "widgets", "ontology_class": NS + "W"})
    assert install_pending_seed(mcp) is True
    assert await mcp.get_resource("seed://late-mcp/seed.ttl") is not None
    assert install_pending_seed(mcp) is False  # already served
