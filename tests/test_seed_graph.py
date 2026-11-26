"""The connector seed graph model, emission and refusals (R068)."""

from __future__ import annotations

import pytest

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
