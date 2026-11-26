"""A virtual_graph resource declares its dialect and capabilities (R912)."""

from __future__ import annotations

import pytest

from scion_sdk.manifest.resource_descriptor_model import (
    VIRTUAL_GRAPH_CAPABILITIES,
    VIRTUAL_GRAPH_DIALECTS,
    ResourceDeclarationError,
    ResourceDescriptor,
)


def _descriptor(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "orders",
        "uri": "resource://orders",
        "kind": "data",
        "origin": "declared",
        "ontology_class": "Order",
        "mapping_status": "mapped",
        "access_kind": "virtual_graph",
        "operation": "select",
        "key_field": "id",
        "pagination": "cursor",
        "dialect": "mssql",
        "capabilities": ["projection", "limit"],
    }
    base.update(over)
    return base


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R912")
def test_dialect_and_capabilities_are_validated() -> None:
    assert "capture" in VIRTUAL_GRAPH_CAPABILITIES
    for dialect in sorted(VIRTUAL_GRAPH_DIALECTS):
        built = ResourceDescriptor.from_json(_descriptor(dialect=dialect))
        assert built.to_json()["dialect"] == dialect
        assert built.to_json()["capabilities"] == ["projection", "limit"]
    for bad in (
        _descriptor(dialect="mysql"),
        _descriptor(dialect=None),
        _descriptor(capabilities=["teleport"]),
        _descriptor(access_kind="mcp_tool"),
    ):
        with pytest.raises(ResourceDeclarationError) as caught:
            ResourceDescriptor.from_json(bad)
        assert caught.value.code == "CONTRACT_DIALECT_UNKNOWN"
