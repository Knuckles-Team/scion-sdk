"""The strategy research record sink (SCION-FINANCE-SOURCES-R903).

The emerald journal's finance research records — debate rounds, trade
journal entries, sentiment fusion, geopolitical risk and strategy cards —
are written to the finance record entries of
MEDULLA-FINANCE-PRIMITIVES-R751.754 through the governed write path of
:mod:`scion_sdk.writeback.service`. Each record model mirrors the medulla
wire fields of MEDULLA-FINANCE-PRIMITIVES-R751.750. The sink sends
components and evidence ids only: the derived score is computed by the
engine (MEDULLA-FINANCE-PRIMITIVES-R751.751), and a caller-supplied score
is refused at validation. One idempotent record per call, keyed by the
record id; stable engine refusal codes (for example
``SCORE_EVIDENCE_MISSING``) pass through unchanged.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import datetime
from typing import Literal, Self

from medulla.generated.write_back import (
    SourceChangeSet,
    WriteBackAttempt,
    WriteBackAuthorizationDecision,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scion_sdk.writeback.service import GovernedWriteBack

__all__ = [
    "BIAS_KINDS",
    "DEBATE_SIDES",
    "LICENCE_CLASSES",
    "BiasAudit",
    "BiasKind",
    "DebateRoundRecord",
    "DebateSide",
    "EvidenceComponent",
    "GeopoliticalRiskRecord",
    "LicenceClass",
    "ResearchRecordSink",
    "SentimentFusionRecord",
    "StrategyCardRecord",
    "TradeJournalEntryRecord",
]

DebateSide = Literal["bull", "bear"]
DEBATE_SIDES: tuple[DebateSide, ...] = ("bull", "bear")

BiasKind = Literal["recency", "confirmation", "loss_aversion", "overconfidence"]
BIAS_KINDS: tuple[BiasKind, ...] = (
    "recency",
    "confirmation",
    "loss_aversion",
    "overconfidence",
)

LicenceClass = Literal["open", "restricted", "proprietary"]
LICENCE_CLASSES: tuple[LicenceClass, ...] = ("open", "restricted", "proprietary")


class _ResearchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class EvidenceComponent(_ResearchRecord):
    """One fusion component: source, component score, weight, evidence id."""

    source: str = Field(min_length=1)
    score: float
    weight: float = Field(gt=0)
    evidence_ref: str = Field(alias="evidenceRef", min_length=1)


class DebateRoundRecord(_ResearchRecord):
    """One round of a trading debate (MEDULLA-FINANCE-PRIMITIVES-R751.750)."""

    debate_id: str = Field(alias="debateId", min_length=1)
    round_index: int = Field(alias="roundIndex", ge=0)
    side: DebateSide
    argument: str = Field(min_length=1)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class BiasAudit(_ResearchRecord):
    """One audited cognitive bias on a journal entry."""

    bias_kind: BiasKind = Field(alias="biasKind")
    finding: str = Field(min_length=1)
    evidence_refs: list[str] = Field(alias="evidenceRefs", min_length=1)


class TradeJournalEntryRecord(_ResearchRecord):
    """One trade journal entry with its bias audits."""

    journal_id: str = Field(alias="journalId", min_length=1)
    entry_at: datetime = Field(alias="entryAt")
    rationale: str = Field(min_length=1)
    strategy_run_ref: str | None = Field(alias="strategyRunRef", default=None)
    bias_audits: list[BiasAudit] = Field(alias="biasAudits")


class SentimentFusionRecord(_ResearchRecord):
    """Sentiment components for one listing; the fused score is engine-side."""

    subject_listing: str = Field(alias="subjectListing", min_length=1)
    components: list[EvidenceComponent] = Field(min_length=1)

    @model_validator(mode="after")
    def _component_scores(self) -> Self:
        _check_components(self.components, low=-1.0, high=1.0)
        return self


class GeopoliticalRiskRecord(_ResearchRecord):
    """Risk components for one region; the fused score is engine-side."""

    region: str = Field(min_length=1)
    components: list[EvidenceComponent] = Field(min_length=1)

    @model_validator(mode="after")
    def _component_scores(self) -> Self:
        _check_components(self.components, low=0.0, high=1.0)
        return self


class StrategyCardRecord(_ResearchRecord):
    """One strategy card bound to its spec and licence class."""

    card_id: str = Field(alias="cardId", min_length=1)
    strategy_spec_ref: str = Field(alias="strategySpecRef", min_length=1)
    licence_class: LicenceClass = Field(alias="licenceClass")
    summary: str = Field(min_length=1)


def _check_components(
    components: list[EvidenceComponent], *, low: float, high: float
) -> None:
    """Mirror the engine's stable refusal (R751.752) before any write."""
    for component in components:
        if not math.isfinite(component.score):
            raise ValueError("SCORE_RANGE_INVALID: component scores must be finite")
        if not low <= component.score <= high:
            raise ValueError(
                f"SCORE_RANGE_INVALID: component score outside [{low}, {high}]"
            )


class ResearchRecordSink:
    """Writes the research records through the governed write path.

    One idempotent record per call, keyed by the record id. A refusal from
    the governed path — SDK- or engine-side — propagates to the caller
    unchanged; nothing is retried or rewritten.
    """

    def __init__(
        self,
        write_back: GovernedWriteBack,
        *,
        tenant_id: str,
        actor: str,
        connector_id: str,
        source_instance_id: str,
        required_capability: str,
        policy_digest: str,
        ttl_ms: int = 3_600_000,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._write_back = write_back
        self._tenant_id = tenant_id
        self._actor = actor
        self._connector_id = connector_id
        self._source_instance_id = source_instance_id
        self._required_capability = required_capability
        self._policy_digest = policy_digest
        self._ttl_ms = ttl_ms
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    async def write_debate_round(
        self,
        record: DebateRoundRecord,
        /,
        *,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> WriteBackAttempt:
        """Write one debate round, idempotently keyed by its debate id."""
        return await self._write(
            "debate_round",
            record.debate_id,
            record,
            authorization,
            base_source_version,
        )

    async def write_journal_entry(
        self,
        record: TradeJournalEntryRecord,
        /,
        *,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> WriteBackAttempt:
        """Write one journal entry, idempotently keyed by its journal id."""
        return await self._write(
            "journal_entry",
            record.journal_id,
            record,
            authorization,
            base_source_version,
        )

    async def write_sentiment(
        self,
        record: SentimentFusionRecord,
        /,
        *,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> WriteBackAttempt:
        """Write one sentiment record, keyed by the subject listing."""
        return await self._write(
            "sentiment",
            record.subject_listing,
            record,
            authorization,
            base_source_version,
        )

    async def write_geopolitical_risk(
        self,
        record: GeopoliticalRiskRecord,
        /,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> WriteBackAttempt:
        """Write one geopolitical risk record, keyed by its region."""
        return await self._write(
            "geopolitical_risk",
            record.region,
            record,
            authorization,
            base_source_version,
        )

    async def write_strategy_card(
        self,
        record: StrategyCardRecord,
        /,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> WriteBackAttempt:
        """Write one strategy card, idempotently keyed by its card id."""
        return await self._write(
            "strategy_card",
            record.card_id,
            record,
            authorization,
            base_source_version,
        )

    async def _write(
        self,
        kind: str,
        record_id: str,
        record: _ResearchRecord,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> WriteBackAttempt:
        change_set = self._change_set(
            kind, record_id, record, authorization, base_source_version
        )
        return await self._write_back.apply(change_set)

    def _change_set(
        self,
        kind: str,
        record_id: str,
        record: _ResearchRecord,
        authorization: WriteBackAuthorizationDecision,
        base_source_version: str,
    ) -> SourceChangeSet:
        patch = record.model_dump(mode="json", by_alias=True)
        idempotency_key = f"finance-record:{kind}:{record_id}"
        draft = SourceChangeSet(
            schema_version=1,
            change_set_id=idempotency_key,
            change_set_digest="0" * 64,
            tenant_id=self._tenant_id,
            actor=self._actor,
            purpose=f"write finance research record {kind}",
            connector_id=self._connector_id,
            source_instance_id=self._source_instance_id,
            entity_id=record_id,
            base_source_version=base_source_version,
            desired_patch=patch,
            field_scope=sorted(patch),
            source_of_truth_rule=(
                "the engine computes the derived score; components and "
                "evidence ids are the record body"
            ),
            field_provenance={field: f"research:{kind}:{field}" for field in patch},
            required_capability=self._required_capability,
            policy_digest=self._policy_digest,
            authorization=authorization,
            idempotency_key=idempotency_key,
            expires_at_ms=self._clock_ms() + self._ttl_ms,
            reconciliation_procedure=(
                f"read the {kind} record by id; an identical body means "
                "the write applied"
            ),
        )
        return draft.model_copy(update={"change_set_digest": draft.canonical_digest()})
