"""Paper maintenance orders execute as one reasoned delta batch (SC-R015)."""

from __future__ import annotations

import asyncio
import json

import pytest
from medulla.generated.source_ingestion import (
    SourceCheckpoint,
    SourceIngestStatus,
)
from pydantic import ValidationError

from scion_sdk.adapters.paper_maintenance import (
    SHORTFALL_REASON,
    MaintenanceRefusal,
    MaintenanceRefusalCode,
    PaperMaintenanceOrder,
    PaperMaintenanceReceipt,
    execute_paper_maintenance,
)
from scion_sdk.testing.sinks import _ingestion_receipt

CHECKPOINT = SourceCheckpoint(stream="papers", position="p1", content_hash="a" * 64)
REVISION = "b" * 64


class RecordingSink:
    def __init__(self, *, digest=REVISION, down=False, drop=()):
        self.batches = []
        self.digest = digest
        self.down = down
        self.drop = set(drop)

    async def source_status(self, connector, stream):
        if self.down:
            raise ConnectionError("engine away")
        return SourceIngestStatus(
            connector=connector,
            stream=stream,
            accepted_checkpoint=CHECKPOINT,
            accepted_checkpoint_digest=self.digest,
        )

    async def submit(self, batch):
        self.batches.append(batch)
        receipt = _ingestion_receipt(batch, affected=0, disposition=_commit())
        tombstones = [
            _tomb(
                {
                    "entity": w.entity.model_dump(),
                    "node_id": f"n:{w.entity.record_id}",
                    "reason": w.reason,
                }
            )
            for w in batch.withdrawals
            if w.entity.record_id not in self.drop
        ]
        return receipt.model_copy(
            update={
                "tombstones": tombstones,
                "tombstoned_count": len(tombstones),
                "receipt_digest": "c" * 64,
            }
        )


def _commit():
    from medulla.generated.source_ingestion import SourceIngestionDisposition

    return SourceIngestionDisposition.COMMITTED


def _tomb(data):
    from medulla.generated.source_ingestion import SourceTombstoneReceipt

    return SourceTombstoneReceipt(**data)


def _order(**over):
    base = {
        "order_id": "o1",
        "tenant_id": "t",
        "goal_id": "g",
        "candidate_ids": ("doi:1", "doi:2"),
        "expected_revision": REVISION,
        "cost_ceiling": 2,
        "owner_authorization_ref": "auth:1",
        "admission_verdict_digest": "d" * 64,
        "issued_at_ms": 1,
        "expires_at_ms": 1000,
    }
    base.update(over)
    return PaperMaintenanceOrder(**base)


def _run(order, sink, now=10):
    return asyncio.run(
        execute_paper_maintenance(order, sink, "scholarx", "papers", now)
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.1")
def test_order_and_receipt_models_reject_invalid_bounds():
    with pytest.raises(ValidationError):
        _order(candidate_ids=())
    with pytest.raises(ValidationError):
        _order(candidate_ids=("a", "a"))
    with pytest.raises(ValidationError):
        _order(cost_ceiling=1)
    with pytest.raises(ValidationError):
        _order(expected_revision="short")
    with pytest.raises(ValidationError):
        _order(candidate_ids=tuple(f"p{i}" for i in range(1025)), cost_ceiling=2000)


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.2")
def test_execution_builds_one_delta_batch_with_reasoned_withdrawals():
    sink = RecordingSink()
    receipt = _run(_order(), sink)
    (batch,) = sink.batches
    assert batch.mode.value == "delta"
    assert batch.provider_checkpoint == CHECKPOINT.model_copy(
        update={"position": "maintenance:o1"}
    )
    assert batch.expected_previous_checkpoint == CHECKPOINT
    assert [w.entity.record_id for w in batch.withdrawals] == ["doi:1", "doi:2"]
    assert {w.reason for w in batch.withdrawals} == {SHORTFALL_REASON}
    assert receipt.withdrawn_ids == ("doi:1", "doi:2")


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.4")
@pytest.mark.parametrize(
    ("code", "order", "sink", "now"),
    [
        (MaintenanceRefusalCode.ORDER_EXPIRED, {}, {}, 1000),
        (MaintenanceRefusalCode.STALE_REVISION, {}, {"digest": "e" * 64}, 10),
        (
            MaintenanceRefusalCode.AUTHORIZATION_MISSING,
            {"owner_authorization_ref": " "},
            {},
            10,
        ),
        (
            MaintenanceRefusalCode.AUTHORIZATION_MISSING,
            {"admission_verdict_digest": ""},
            {},
            10,
        ),
        (MaintenanceRefusalCode.SINK_UNAVAILABLE, {}, {"down": True}, 10),
    ],
)
def test_each_refusal_submits_nothing(code, order, sink, now):
    recording = RecordingSink(**sink)
    with pytest.raises(MaintenanceRefusal) as raised:
        _run(_order(**order), recording, now)
    assert raised.value.code is code
    assert recording.batches == []


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.4")
def test_over_ceiling_and_empty_are_refused_before_submit():
    sink = RecordingSink()
    over = _order().model_copy(update={"cost_ceiling": 1})
    with pytest.raises(MaintenanceRefusal) as raised:
        _run(over, sink)
    assert raised.value.code is MaintenanceRefusalCode.OVER_CEILING
    empty = _order().model_copy(update={"candidate_ids": ()})
    with pytest.raises(MaintenanceRefusal) as raised:
        _run(empty, sink)
    assert raised.value.code is MaintenanceRefusalCode.CANDIDATES_EMPTY
    assert sink.batches == []


@pytest.mark.spec("SCION-SOURCE-INGEST-R015.5")
def test_receipt_binds_order_and_engine_receipt_without_internals():
    receipt = _run(_order(), RecordingSink(drop={"doi:2"}))
    assert isinstance(receipt, PaperMaintenanceReceipt)
    assert receipt.order_id == "o1" and receipt.goal_id == "g"
    assert receipt.candidate_count == 2
    assert receipt.withdrawn_ids == ("doi:1",)
    assert receipt.refused == (("doi:2", "NOT_WITHDRAWN"),)
    assert receipt.engine_receipt_digest == "c" * 64
    assert receipt.reason == SHORTFALL_REASON
    text = json.dumps(receipt.model_dump(mode="json"))
    assert "/" not in text.replace("doi:", "")
