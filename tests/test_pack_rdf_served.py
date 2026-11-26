"""A declaration reaches the real engine as one contract pack (SCION-CONNECTOR-CONTROL-R067.8)."""

from __future__ import annotations

import pytest
from live_engine import live_ingest, scoped_client  # noqa: F401

from scion_sdk.manifest.pack_preflight import preflight_pack_turtle
from scion_sdk.manifest.pack_turtle import (
    declaration_digest,
    emit_pack_turtle,
    load_pack_declaration,
)
from scion_sdk.manifest.views import (
    MappingViewDrift,
    check_mapping_view,
    read_certification_record,
    write_certification_record,
    write_mapping_view,
)
from tests.test_pack_turtle import DOC


async def _certify(client, declaration, root) -> str:
    turtle = emit_pack_turtle(declaration)
    await preflight_pack_turtle(client.rdf, turtle)
    check_mapping_view(root, declaration)
    await client.rdf.add_triples(ntriples=turtle)
    rows = await client.rdf.sparql(
        "SELECT ?digest WHERE { ?c a <https://knuckles-team.github.io/ontology/"
        "connector-pack#IngestContract> ; <https://knuckles-team.github.io/ontology/"
        "connector-pack#contractDigest> ?digest }"
    )
    (row,) = rows
    write_certification_record(root, declaration, str(row["digest"]))
    return str(row["digest"])


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R067.8")
async def test_declaration_to_engine_pack_end_to_end(live_ingest, tmp_path) -> None:  # noqa: F811
    client = await scoped_client(
        [
            "validation:read",
            "sparql:read",
            "graph:read",
            "graph:write",
            "rdf:read",
            "rdf:write",
        ]
    )
    try:
        declaration = load_pack_declaration(DOC)
        write_mapping_view(tmp_path, declaration)
        first = await _certify(client, declaration, tmp_path)
        assert read_certification_record(tmp_path)["contract_digest"] == first

        changed = load_pack_declaration({**DOC, "contract_version": "2"})
        changed_turtle = emit_pack_turtle(changed)
        await preflight_pack_turtle(client.rdf, changed_turtle)
        assert changed_turtle != emit_pack_turtle(declaration)
        assert declaration_digest(changed) != first
        write_mapping_view(tmp_path, changed)

        view = tmp_path / "ontology" / "mappings" / "source.yaml"
        view.write_text(view.read_text() + "# hand edit\n")
        with pytest.raises(MappingViewDrift):
            check_mapping_view(tmp_path, changed)
    finally:
        await client.close()
