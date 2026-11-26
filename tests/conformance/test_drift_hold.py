"""The runner holds the checkpoint when the engine quarantines a page (R007.1)."""

from __future__ import annotations

from typing import Any

import pytest
from medulla.generated.source_ingestion import (
    SourceCheckpoint,
    SourceIngestionMode,
    SourceIngestionReceipt,
    SourceIngestionRequest,
)

from scion_sdk.contracts import RecordPage
from scion_sdk.engine_capability import (
    EngineCapabilityUnavailable,
    engine_capability_present,
)
from scion_sdk.runner.errors import EngineSchemaDriftQuarantined
from scion_sdk.runner.syncing import SyncTarget, commit_page
from scion_sdk.schema_drift import SchemaContract

CONTRACT = SchemaContract.from_schema(
    {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
    identifier_fields=("id",),
)
CHECKPOINT = SourceCheckpoint(stream="items", position={"page": 1})


class _Refusal(RuntimeError):
    def __init__(self, **fields: Any) -> None:
        super().__init__("SCHEMA_DRIFT_QUARANTINED")
        self.code = "SCHEMA_DRIFT_QUARANTINED"
        for name, value in fields.items():
            setattr(self, name, value)


class _QuarantiningSink:
    def __init__(self, **fields: Any) -> None:
        self.fields = fields
        self.submitted = 0

    async def submit(self, batch: SourceIngestionRequest) -> SourceIngestionReceipt:
        self.submitted += 1
        raise _Refusal(**self.fields)


def _page() -> RecordPage:
    return RecordPage(
        records=(),
        mode=SourceIngestionMode.DELTA,
        strict_schema=False,
        checkpoint=CHECKPOINT,
        exhausted=False,
        schema_contract=CONTRACT,
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R007.1")
async def test_quarantine_without_the_engine_report_is_refused_by_capability() -> None:
    """A pinned engine error without ``drift`` is a typed capability refusal."""
    sink = _QuarantiningSink()
    target = SyncTarget("synthetic", sink, 1, schema_contract=CONTRACT)  # type: ignore[arg-type]
    with pytest.raises(EngineCapabilityUnavailable) as caught:
        await commit_page(_page(), target, expected_previous_checkpoint=None)
    assert caught.value.method == "SchemaDriftQuarantinedError.drift"
    assert sink.submitted == 1


@pytest.mark.skipif(
    not hasattr(__import__("medulla"), "SchemaDriftQuarantinedError")
    or not engine_capability_present("SchemaDriftQuarantinedError.drift"),
    reason="runs once the pinned engine client serves the drift report",
)
async def test_checkpoint_held_on_engine_quarantine() -> None:
    drift = [{"stream": "items", "report": {"class": "breaking"}}]
    sink = _QuarantiningSink(drift=drift)
    target = SyncTarget("synthetic", sink, 1, schema_contract=CONTRACT)  # type: ignore[arg-type]
    with pytest.raises(EngineSchemaDriftQuarantined) as caught:
        await commit_page(_page(), target, expected_previous_checkpoint=None)
    assert caught.value.drift == drift
    assert len(caught.value.drift_digest) == 64
