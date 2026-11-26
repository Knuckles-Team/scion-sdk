"""Execute one admitted paper maintenance order as engine withdrawals (SC-R015).

Medulla decides shortfall and candidates; arbora admits the order. The SDK only
turns an admitted order into one whole-or-nothing delta batch of withdrawals.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from medulla.generated.source_ingestion import (
    SourceEntityRef,
    SourceIngestionMode,
    SourceIngestionRequest,
    SourceWithdrawal,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scion_sdk.ports.sink import Sink

__all__ = [
    "SHORTFALL_REASON",
    "MaintenanceRefusal",
    "MaintenanceRefusalCode",
    "PaperMaintenanceOrder",
    "PaperMaintenanceReceipt",
    "execute_paper_maintenance",
]

SHORTFALL_REASON = "paper_shortfall_purge"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MaintenanceRefusalCode(StrEnum):
    ORDER_EXPIRED = "ORDER_EXPIRED"
    STALE_REVISION = "STALE_REVISION"
    OVER_CEILING = "OVER_CEILING"
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
    CANDIDATES_EMPTY = "CANDIDATES_EMPTY"
    SINK_UNAVAILABLE = "SINK_UNAVAILABLE"


class MaintenanceRefusal(Exception):
    """A closed refusal raised before any withdrawal is submitted."""

    def __init__(self, code: MaintenanceRefusalCode) -> None:
        super().__init__(code.value)
        self.code = code


class PaperMaintenanceOrder(BaseModel):
    """One admitted order: ids to withdraw under a cost ceiling and a revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=1024)
    expected_revision: Digest
    cost_ceiling: int = Field(ge=1)
    owner_authorization_ref: str
    admission_verdict_digest: str
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _bounded(self) -> PaperMaintenanceOrder:
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate ids must be unique")
        if self.cost_ceiling < len(self.candidate_ids):
            raise ValueError("cost ceiling is below the candidate count")
        return self


class PaperMaintenanceReceipt(BaseModel):
    """What the engine withdrew for one order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    goal_id: str
    candidate_count: int = Field(ge=0)
    withdrawn_ids: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]
    reason: str = SHORTFALL_REASON
    engine_receipt_id: str
    engine_receipt_digest: Digest
    completed_at_ms: int = Field(ge=0)


def _check(order: PaperMaintenanceOrder, now_ms: int) -> None:
    if not order.owner_authorization_ref.strip() or not _DIGEST.match(
        order.admission_verdict_digest
    ):
        raise MaintenanceRefusal(MaintenanceRefusalCode.AUTHORIZATION_MISSING)
    if not order.candidate_ids:
        raise MaintenanceRefusal(MaintenanceRefusalCode.CANDIDATES_EMPTY)
    if len(order.candidate_ids) > order.cost_ceiling:
        raise MaintenanceRefusal(MaintenanceRefusalCode.OVER_CEILING)
    if now_ms >= order.expires_at_ms:
        raise MaintenanceRefusal(MaintenanceRefusalCode.ORDER_EXPIRED)


async def execute_paper_maintenance(
    order: PaperMaintenanceOrder,
    sink: Sink,
    connector: str,
    stream: str,
    now_ms: int,
) -> PaperMaintenanceReceipt:
    """Withdraw exactly the order's candidates in one delta batch."""
    _check(order, now_ms)
    try:
        status = await sink.source_status(connector, stream)
    except Exception as error:
        raise MaintenanceRefusal(MaintenanceRefusalCode.SINK_UNAVAILABLE) from error
    if (
        status.accepted_checkpoint is None
        or status.accepted_checkpoint_digest != order.expected_revision
    ):
        raise MaintenanceRefusal(MaintenanceRefusalCode.STALE_REVISION)
    batch = SourceIngestionRequest(
        connector=connector,
        mode=SourceIngestionMode.DELTA,
        provider_checkpoint=status.accepted_checkpoint.model_copy(
            update={"position": f"maintenance:{order.order_id}"}
        ),
        expected_previous_checkpoint=status.accepted_checkpoint,
        records=[],
        withdrawals=[
            SourceWithdrawal(
                entity=SourceEntityRef(record_id=paper_id, stream=stream),
                reason=SHORTFALL_REASON,
            )
            for paper_id in order.candidate_ids
        ],
        strict_schema=True,
    )
    receipt = await sink.submit(batch)
    withdrawn = {t.entity.record_id for t in receipt.tombstones}
    return PaperMaintenanceReceipt(
        order_id=order.order_id,
        goal_id=order.goal_id,
        candidate_count=len(order.candidate_ids),
        withdrawn_ids=tuple(i for i in order.candidate_ids if i in withdrawn),
        refused=tuple(
            (i, "NOT_WITHDRAWN") for i in order.candidate_ids if i not in withdrawn
        ),
        engine_receipt_id=receipt.receipt_id,
        engine_receipt_digest=receipt.receipt_digest,
        completed_at_ms=now_ms,
    )
