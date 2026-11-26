"""The served descriptor of a SQL or document connector declares its dialect (R912)."""

from __future__ import annotations

import pytest

from scion_sdk.manifest.resource_descriptor_model import ResourceDescriptor


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R912")
def test_descriptor_declares_dialect() -> None:
    for dialect in ("mssql", "oracle", "db2", "mongodb", "documentdb"):
        served = ResourceDescriptor.from_json(
            {
                "name": "orders",
                "uri": "resource://orders",
                "origin": "declared",
                "ontology_class": "Order",
                "mapping_status": "mapped",
                "access_kind": "virtual_graph",
                "operation": "select",
                "key_field": "id",
                "pagination": "cursor",
                "dialect": dialect,
                "capabilities": ["projection", "predicate"],
            }
        ).to_json()
        again = ResourceDescriptor.from_json(served)
        assert again.dialect == dialect and again.capabilities
