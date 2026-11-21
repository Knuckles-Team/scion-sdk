"""SCION-CENTRALIZED-CAPABILITIES-R048: the SDK follows the medulla 3.3.x client contracts."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest
from medulla.generated.source_ingestion import (
    SourceCheckpoint,
    SourceIngestionDisposition,
    SourceIngestionReceipt,
)
from packaging.requirements import Requirement
from packaging.version import Version
from test_contracts_and_sink import _batch, _record

from scion_sdk.testing.sinks import InMemorySink

ROOT = Path(__file__).resolve().parents[1]
FLOOR = Version("3.3.4")
REQUIRED_RECEIPT_FIELDS = {"classes", "governance", "report", "stream_object_id"}


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R048")
def test_engine_floor_is_the_3_3_client_and_the_install_meets_it() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    engine = [
        Requirement(text)
        for text in project["dependencies"]
        if Requirement(text).name == "medulla"
    ]
    assert len(engine) == 1
    assert engine[0].specifier.contains(FLOOR)
    assert not engine[0].specifier.contains("3.3.3")
    assert Version(version("medulla")) >= FLOOR


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R048")
async def test_in_memory_sink_receipts_carry_every_required_3_3_field() -> None:
    required = {
        name
        for name, field in SourceIngestionReceipt.model_fields.items()
        if field.is_required()
    }
    assert required >= REQUIRED_RECEIPT_FIELDS
    sink = InMemorySink()
    batch = _batch(
        _record(title="t"),
        checkpoint=SourceCheckpoint(stream="demo", position={}, watermark="w"),
    )
    first = await sink.submit(batch)
    replay = await sink.submit(batch)
    for receipt in (first, replay):
        assert SourceIngestionReceipt.model_validate(receipt.model_dump(mode="python"))
        assert receipt.stream_object_id
        assert receipt.classes == []
        assert receipt.governance.refused == 0
    assert first.disposition is SourceIngestionDisposition.COMMITTED
    assert (first.report.written, first.report.unchanged) == (1, 0)
    assert replay.disposition is SourceIngestionDisposition.REPLAYED
    assert (replay.report.written, replay.report.unchanged) == (0, 1)
