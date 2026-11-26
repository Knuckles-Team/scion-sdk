"""ConnectorPackDeclaration: typed model, round trip, nested-property refusal."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scion_sdk.manifest.pack_declaration import (
    AccessPolicy,
    ConnectorPackDeclaration,
    CursorPolicy,
    DeclaredNode,
    FieldMapping,
    SourceObject,
)

_DECLARATION = {
    "connector": "widget-mcp",
    "contract_version": "1",
    "nodes": [
        {
            "kind": "tool",
            "name": "list_widgets",
            "domain_class_iri": "https://knuckles-team.github.io/ontology/core#Widget",
        }
    ],
    "sources": [
        {
            "name": "widgets",
            "class_iri": "https://knuckles-team.github.io/ontology/core#Widget",
            "field_mappings": [
                {
                    "field": "serial",
                    "property_iri": "https://knuckles-team.github.io/ontology/core#serialNumber",
                }
            ],
            "key_fields": ["serial"],
            "cursor": {"mode": "cursor", "field": "next"},
            "access": {"method": "mcp_tool", "operation": "list_widgets"},
            "privacy_classes": ["internal"],
            "provenance_required": True,
            "served_by": ["list_widgets"],
        }
    ],
}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.1")
def test_declaration_round_trip_and_nested_property_refusal() -> None:
    declaration = ConnectorPackDeclaration.model_validate(_DECLARATION)
    again = ConnectorPackDeclaration.model_validate(declaration.model_dump())
    assert again == declaration
    with pytest.raises(ValidationError):
        declaration.connector = "other"  # type: ignore[misc]

    nested = {**_DECLARATION, "sources": [{**_DECLARATION["sources"][0]}]}
    nested["sources"][0]["sourceProperties"] = {"color": "red"}  # type: ignore[index]
    with pytest.raises(ValidationError):
        ConnectorPackDeclaration.model_validate(nested)

    dangling = {**_DECLARATION, "sources": [{**_DECLARATION["sources"][0]}]}
    dangling["sources"][0]["served_by"] = ["missing_tool"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="undeclared nodes"):
        ConnectorPackDeclaration.model_validate(dangling)

    unmapped = {**_DECLARATION, "sources": [{**_DECLARATION["sources"][0]}]}
    unmapped["sources"][0]["key_fields"] = ["id"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="no field mapping"):
        ConnectorPackDeclaration.model_validate(unmapped)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.1")
def test_components_build_a_declaration_directly() -> None:
    source = SourceObject(
        name="widgets",
        class_iri="https://knuckles-team.github.io/ontology/core#Widget",
        field_mappings=(
            FieldMapping(
                field="serial",
                property_iri="https://knuckles-team.github.io/ontology/core#serialNumber",
            ),
        ),
        key_fields=("serial",),
        cursor=CursorPolicy(mode="cursor", field="next"),
        access=AccessPolicy(method="mcp_tool", operation="list_widgets"),
        served_by=("list_widgets",),
    )
    node = DeclaredNode(
        kind="tool",
        name="list_widgets",
        domain_class_iri="https://knuckles-team.github.io/ontology/core#Widget",
    )
    declaration = ConnectorPackDeclaration(
        connector="widget-mcp",
        contract_version="1",
        sources=(source,),
        nodes=(node,),
    )
    assert declaration.sources[0].cursor.mode == "cursor"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.6")
def test_declaration_refusals_name_the_field() -> None:
    from scion_sdk.manifest.pack_turtle import (
        PackDeclarationRefusal,
        load_pack_declaration,
    )

    def refused(mutate) -> PackDeclarationRefusal:
        import copy

        document = copy.deepcopy(_DECLARATION)
        mutate(document)
        with pytest.raises(PackDeclarationRefusal) as caught:
            load_pack_declaration(document)
        return caught.value

    nested = refused(
        lambda d: d["sources"][0].update({"sourceProperties": {"a": {"b": 1}}})
    )
    assert (nested.code, nested.field) == (
        "nested_property",
        "sources.0.sourceProperties",
    )
    klass = refused(lambda d: d["sources"][0].update({"class_iri": "Widget"}))
    assert (klass.code, klass.field) == ("unresolved_class", "sources.widgets")
    mapping = {
        "field": "serial2",
        "property_iri": _DECLARATION["sources"][0]["field_mappings"][0]["property_iri"],
    }
    dup = refused(lambda d: d["sources"][0]["field_mappings"].append(mapping))
    assert (dup.code, dup.field) == (
        "duplicate_mapping",
        "sources.widgets.property_iri",
    )
    key = refused(lambda d: d["sources"][0].update({"key_fields": []}))
    assert (key.code, key.field) == ("missing_key_rule", "sources.widgets")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.3")
def test_views_render_and_fingerprint_id_is_sdk_owned(tmp_path) -> None:
    from scion_sdk.manifest.pack_turtle import load_pack_declaration
    from scion_sdk.manifest.tool_schema import (
        COMPATIBILITY_FINGERPRINT_ALGORITHM,
        compatibility_fingerprint,
        schema_fingerprint,
    )
    from scion_sdk.manifest.views import (
        MAPPING_VIEW_FILE,
        MappingViewDrift,
        check_mapping_view,
        render_mapping_view,
        write_mapping_view,
    )

    declaration = load_pack_declaration(_DECLARATION)
    target = write_mapping_view(tmp_path, declaration)
    assert target == tmp_path / MAPPING_VIEW_FILE
    assert target.read_text() == render_mapping_view(declaration)
    check_mapping_view(tmp_path, declaration)
    target.write_text(target.read_text() + "# hand edit\n")
    with pytest.raises(MappingViewDrift):
        check_mapping_view(tmp_path, declaration)
    assert COMPATIBILITY_FINGERPRINT_ALGORITHM == "scion-sdk:mcp-tool-contract-compat"
    assert compatibility_fingerprint("t", {"type": "object"}) != schema_fingerprint(
        "t", {"type": "object"}
    )
    import scion_sdk.manifest.tool_schema as tool_schema

    assert not hasattr(tool_schema, "_LEGACY_COMPATIBILITY_DOMAIN")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.7")
def test_certification_records_accepted_contract_digest(tmp_path) -> None:
    from scion_sdk.manifest.pack_turtle import declaration_digest, load_pack_declaration
    from scion_sdk.manifest.views import (
        CERTIFICATION_FILE,
        read_certification_record,
        write_certification_record,
    )

    declaration = load_pack_declaration(_DECLARATION)
    digest = declaration_digest(declaration)
    assert len(digest) == 64 and digest == digest.lower()
    write_certification_record(tmp_path, declaration, digest)
    assert (tmp_path / CERTIFICATION_FILE).is_file()
    record = read_certification_record(tmp_path)
    assert record == {
        "connector": "widget-mcp",
        "contract_digest": digest,
        "declaration_digest": digest,
    }
    with pytest.raises(ValueError):
        write_certification_record(tmp_path, declaration, "ABC")
