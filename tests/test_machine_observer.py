"""The machine observer helper: one place builds the ``observes`` link to a key anchor."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from medulla import engine as _real_engine

from scion_sdk import machine
from scion_sdk.ingest import ChangeSet, Entity, IngestBinding
from scion_sdk.ingest.request import build_request

# The key anchor vectors of the machine inventory data model (kind, normalized value, anchor id).
VECTORS = (
    (
        "uuid",
        "123e4567-e89b-12d3-a456-426614174000",
        "urn:machine-key:uuid:fadf2b3c7a3b8eaf74b9d3045973e1ce",
    ),
    ("serial", "ABC 123", "urn:machine-key:serial:ce4057006ad7685b67a799f0743e2911"),
    (
        "bmc",
        "aa:bb:cc:dd:ee:01",
        "urn:machine-key:bmc:84d46b5bb6df3515f700478db20f68e2",
    ),
    ("host", "node-1.example", "urn:machine-key:host:11165384ff7580ae5652b6a2bae3f5dd"),
    ("host", "alpha", "urn:machine-key:host:41097ff758a2bcf28635c4da73d9fa18"),
)


def _reference_ref(kind: str, raw: str) -> str:
    """The engine function's contract, for a wheel that does not export it yet."""
    value = raw.strip()
    if not value:
        raise ValueError(f"{kind}:empty")
    if value.lower() in {"unknown", "none", "n/a", "to be filled by o.e.m."}:
        raise ValueError(f"{kind}:placeholder")
    digest = hashlib.sha256(f"{kind}\n{value}".encode()).hexdigest()[:32]
    return json.dumps(
        {"iri": f"urn:machine-key:{kind}:{digest}", "key": f"{kind}:{value}"}
    )


@pytest.fixture(autouse=True)
def _engine(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(_real_engine, "machine_key_ref"):
        monkeypatch.setattr(
            _real_engine, "machine_key_ref", _reference_ref, raising=False
        )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R057.1")
@pytest.mark.parametrize(("kind", "value", "anchor"), VECTORS)
def test_observes_machine_targets_the_key_anchor_of_the_vector(
    kind: Any, value: str, anchor: str
) -> None:
    link = machine.observes_machine("host:1", kind, value)
    assert (link.source, link.target, link.relationship) == (
        "host:1",
        anchor,
        "observes",
    )
    assert machine.machine_key_anchor(kind, value) == anchor


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R057.1")
@pytest.mark.parametrize(
    ("kind", "raw", "reason"),
    (
        ("uuid", "", "empty"),
        ("serial", "unknown", "placeholder"),
        ("host", "  ", "empty"),
    ),
)
def test_placeholder_and_malformed_keys_raise_machine_key_refused(
    monkeypatch: pytest.MonkeyPatch, kind: Any, raw: str, reason: str
) -> None:
    if reason == "placeholder" and hasattr(_real_engine, "machine_key_ref"):
        monkeypatch.setattr(_real_engine, "machine_key_ref", _reference_ref)
    with pytest.raises(machine.MachineKeyRefused) as refused:
        machine.machine_key_anchor(kind, raw)
    assert (refused.value.kind, refused.value.reason) == (kind, reason)
    assert isinstance(refused.value, ValueError)


def test_unknown_engine_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(kind: str, raw: str) -> str:
        raise ValueError("engine unavailable")

    monkeypatch.setattr(_real_engine, "machine_key_ref", boom)
    with pytest.raises(ValueError, match="engine unavailable") as caught:
        machine.machine_key_anchor("host", "alpha")
    assert not isinstance(caught.value, machine.MachineKeyRefused)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R057.2")
def test_public_surface_exports_the_helper() -> None:
    kind: machine.MachineKeyKind = "serial"
    assert kind in machine.MachineKeyKind.__args__
    assert set(machine.__all__) >= {
        "observes_machine",
        "OBSERVES_RELATION",
        "MachineKeyRefused",
    }
    assert machine.OBSERVES_RELATION == {
        "name": "observes",
        "label": "observes",
        "target": "MachineKey",
        "lpg_rel_type": "",
    }


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R057")
@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R057.3")
def test_changeset_with_the_helper_builds_a_source_ingestion_request_with_the_observes_reference() -> (
    None
):
    binding = IngestBinding(
        connector="demo-mcp",
        stream="hosts",
        contract_iri="contract:demo-mcp",
        contract_digest="d" * 64,
    )
    changes = ChangeSet(
        entities=(Entity("host:1", "Host", {"name": "alpha"}),),
        relationships=(machine.observes_machine("host:1", "host", "alpha"),),
    )
    request = build_request(binding, changes, previous=None)
    (link,) = request.relationships
    assert link.relation_reference == "connector:demo-mcp#relations/Host/observes"
    assert (
        link.target.record_id == "urn:machine-key:host:41097ff758a2bcf28635c4da73d9fa18"
    )
