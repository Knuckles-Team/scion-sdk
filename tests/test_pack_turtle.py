"""Pack Turtle emission, engine preflight and the contract pack entry."""

from __future__ import annotations

import pytest

from scion_sdk.manifest.pack_declaration import ConnectorPackDeclaration
from scion_sdk.manifest.pack_preflight import (
    PackPreflightRefused,
    preflight_pack_turtle,
)
from scion_sdk.manifest.pack_turtle import (
    CP,
    PackDeclarationRefusal,
    check_pack_declaration,
    declaration_digest,
    emit_pack_turtle,
    load_pack_declaration,
)

WIDGET = "https://knuckles-team.github.io/ontology/core#Widget"
SERIAL = "https://knuckles-team.github.io/ontology/core#serialNumber"
DOC = {
    "connector": "widget-mcp",
    "contract_version": "1",
    "nodes": [
        {"kind": "feature", "name": "widgets", "domain_class_iri": WIDGET},
        {"kind": "tool", "name": "list_widgets", "domain_class_iri": WIDGET},
        {"kind": "skill", "name": "widget-reader", "domain_class_iri": WIDGET},
    ],
    "sources": [
        {
            "name": "widgets",
            "class_iri": WIDGET,
            "field_mappings": [{"field": "serial", "property_iri": SERIAL}],
            "key_fields": ["serial"],
            "cursor": {"mode": "cursor", "field": "next"},
            "privacy_classes": ["internal"],
            "provenance_required": True,
        }
    ],
}


def declaration() -> ConnectorPackDeclaration:
    return load_pack_declaration(DOC)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.2")
def test_emit_is_deterministic_and_uses_engine_terms() -> None:
    first = emit_pack_turtle(declaration())
    assert first == emit_pack_turtle(declaration())
    lines = first.splitlines()
    assert lines == sorted(lines) and "_:" not in first
    for line in lines:
        subject, predicate, *_ = line.split(" ", 2)
        assert subject.startswith("<") and predicate.startswith("<")
    assert "access-contract" not in first and "#AccessContract" not in first
    assert f'<{CP}contractDigest> "{declaration_digest(declaration())}"' in first
    assert f"<{CP}providesFeature>" in first and f"<{CP}realizedBy>" in first
    assert f"<{CP}operatesOn> <{WIDGET}>" in first
    assert f'<{CP}cursorKind> "opaque"' in first
    assert f'<{CP}defaultAcl> "quarantine"' in first


class _Engine:
    def __init__(self, report: dict[str, object]) -> None:
        self.report = report
        self.calls: list[tuple[str, str]] = []

    async def validate_shacl(self, shapes: str, data_graph: str) -> dict[str, object]:
        self.calls.append((shapes, data_graph))
        return self.report


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.4")
async def test_preflight_uses_engine_validation() -> None:
    turtle = emit_pack_turtle(declaration())
    engine = _Engine({"conforms": True})
    check_pack_declaration(declaration())
    assert await preflight_pack_turtle(engine, turtle)  # type: ignore[arg-type] == {"conforms": True}
    shapes, sent = engine.calls[0]
    assert sent == turtle and "NodeShape" in shapes
    refused = _Engine({"conforms": False, "results": [{"code": "SH_MIN_COUNT"}]})
    with pytest.raises(PackPreflightRefused) as caught:
        await preflight_pack_turtle(refused, turtle)  # type: ignore[arg-type]
    assert caught.value.report["results"] == [{"code": "SH_MIN_COUNT"}]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.5")
async def test_content_pack_carries_contract_entry(tmp_path) -> None:
    from scion_sdk.artifacts.pack import build_connector_content_pack
    from scion_sdk.mcp.content import ConnectorContent

    content = ConnectorContent("widget-mcp", tmp_path, "1.0.0")
    pack = await build_connector_content_pack(content, declaration())
    assert "contract://widget-mcp" in pack.resource_uris
    entries = {e.uri: e for e in pack.archive.entries}
    body = pack.archive.data[
        entries["contract://widget-mcp"].body.offset : entries[
            "contract://widget-mcp"
        ].body.offset
        + entries["contract://widget-mcp"].body.length
    ].decode()
    assert body == emit_pack_turtle(declaration())
    other = declaration().model_copy(update={"connector": "other-mcp"})
    from scion_sdk.ports.errors import MalformedArtifactError

    with pytest.raises(MalformedArtifactError):
        await build_connector_content_pack(content, other)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.6")
def test_refusal_codes_are_closed() -> None:
    assert PackDeclarationRefusal.CODES == (
        "unresolved_class",
        "nested_property",
        "duplicate_mapping",
        "missing_key_rule",
    )
    with pytest.raises(ValueError):
        PackDeclarationRefusal("other", "x")
