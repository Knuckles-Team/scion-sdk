"""SCION-FINANCE-SOURCES-R903: the strategy research record sink.

The medulla owner of the other side is MEDULLA-FINANCE-PRIMITIVES-R751.754;
the engine computes the derived scores (R751.751). This test uses a fake
engine for the SDK side only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from medulla.generated.write_back import (
    WriteBackAuthorizationDecision,
    WriteBackAuthorizationMode,
    WriteBackEffectStatus,
)
from pydantic import ValidationError

from scion_sdk.finance.research_records import (
    BIAS_KINDS,
    DEBATE_SIDES,
    LICENCE_CLASSES,
    BiasAudit,
    BiasKind,
    DebateRoundRecord,
    DebateSide,
    EvidenceComponent,
    GeopoliticalRiskRecord,
    LicenceClass,
    ResearchRecordSink,
    SentimentFusionRecord,
    StrategyCardRecord,
    TradeJournalEntryRecord,
)
from scion_sdk.ports.writeback_models import SourceSnapshot
from scion_sdk.testing.writeback_audit import InMemoryAuditReservation
from scion_sdk.writeback.memory import InMemoryWriteBackTransport
from scion_sdk.writeback.service import GovernedWriteBack

pytestmark = pytest.mark.spec("SCION-FINANCE-SOURCES-R903")

ENTRY_AT = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)

#: A fixed millisecond clock so change sets — and their digests — are
#: deterministic across retries.
FIXED_MS = 1_700_000_000_000


def _decision() -> WriteBackAuthorizationDecision:
    return WriteBackAuthorizationDecision(
        mode=WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
        authorization_ref="approval:research-1",
        authorized=True,
        decision_digest="2" * 64,
        input_digest="3" * 64,
        output_digest="4" * 64,
    )


class _ApprovingVerifier:
    """A fake policy verifier returning the change set's bound decision."""

    async def verify(self, change_set: Any) -> WriteBackAuthorizationDecision:
        return change_set.authorization


def _component(
    source: str, score: float, weight: float, evidence_ref: str
) -> EvidenceComponent:
    return EvidenceComponent(
        source=source,
        score=score,
        weight=weight,
        evidence_ref=evidence_ref,
    )


def _sink(transport: InMemoryWriteBackTransport) -> ResearchRecordSink:
    return ResearchRecordSink(
        GovernedWriteBack(
            transport,
            _ApprovingVerifier(),
            InMemoryAuditReservation(),
            clock_ms=lambda: FIXED_MS,
        ),
        tenant_id="tenant-1",
        actor="emerald:journal",
        connector_id="emerald-agent",
        source_instance_id="emerald-research",
        required_capability="finance.record.write",
        policy_digest="d" * 64,
        clock_ms=lambda: FIXED_MS,
    )


async def test_sink_sends_components_not_scores() -> None:
    """Each write is one governed change set carrying components and
    evidence ids only — never a computed score — and rewrites of the same
    record are idempotent."""
    transport = InMemoryWriteBackTransport()
    for entity in ("debate-1", "journal-1", "ACME", "eu-west", "card-1"):
        transport.seed(entity, "v1", {})
    sink = _sink(transport)
    decision = _decision()

    debate = DebateRoundRecord(
        debateId="debate-1",
        roundIndex=1,
        side="bull",
        argument="earnings revision beats the consensus",
        evidenceRefs=["ev-1", "ev-2"],
    )
    attempt = await sink.write_debate_round(
        debate, authorization=decision, base_source_version="v1"
    )
    assert attempt.effect_status is WriteBackEffectStatus.APPLIED
    assert transport.entities["debate-1"].fields == {
        "debateId": "debate-1",
        "roundIndex": 1,
        "side": "bull",
        "argument": "earnings revision beats the consensus",
        "evidenceRefs": ["ev-1", "ev-2"],
    }

    journal = TradeJournalEntryRecord(
        journalId="journal-1",
        entryAt=ENTRY_AT,
        rationale="entered on the breakout",
        strategyRunRef="run-9",
        biasAudits=[
            BiasAudit(
                biasKind="recency",
                finding="weighted the last week too heavily",
                evidenceRefs=["ev-3"],
            )
        ],
    )
    await sink.write_journal_entry(
        journal, authorization=decision, base_source_version="v1"
    )
    fields = transport.entities["journal-1"].fields
    assert fields["journalId"] == "journal-1"
    assert fields["rationale"] == "entered on the breakout"
    assert fields["strategyRunRef"] == "run-9"
    assert fields["biasAudits"][0]["biasKind"] == "recency"
    assert datetime.fromisoformat(fields["entryAt"]) == ENTRY_AT

    sentiment = SentimentFusionRecord(
        subjectListing="ACME",
        components=[
            _component("newswire", -0.2, 0.6, "ev-4"),
            _component("forum", 0.4, 0.4, "ev-5"),
        ],
    )
    first = await sink.write_sentiment(
        sentiment, authorization=decision, base_source_version="v1"
    )
    # The same record again: one idempotent record keyed by the record id.
    attempts_before = transport.attempts
    again = await sink.write_sentiment(
        sentiment, authorization=decision, base_source_version="v1"
    )
    assert again == first
    assert transport.attempts == attempts_before

    fields = transport.entities["ACME"].fields
    # Components and evidence ids only: the fused score is engine-side.
    assert "score" not in fields
    assert fields["subjectListing"] == "ACME"
    assert fields["components"] == [
        {
            "source": "newswire",
            "score": -0.2,
            "weight": 0.6,
            "evidenceRef": "ev-4",
        },
        {"source": "forum", "score": 0.4, "weight": 0.4, "evidenceRef": "ev-5"},
    ]

    risk = GeopoliticalRiskRecord(
        region="eu-west",
        components=[_component("wire", 0.7, 1.0, "ev-6")],
    )
    await sink.write_geopolitical_risk(
        risk, authorization=decision, base_source_version="v1"
    )
    fields = transport.entities["eu-west"].fields
    assert "score" not in fields
    assert fields["region"] == "eu-west"
    assert fields["components"][0]["evidenceRef"] == "ev-6"

    card = StrategyCardRecord(
        cardId="card-1",
        strategySpecRef="spec:momentum-v2",
        licenceClass="restricted",
        summary="trend following over 200d",
    )
    await sink.write_strategy_card(
        card, authorization=decision, base_source_version="v1"
    )
    assert transport.entities["card-1"].fields == {
        "cardId": "card-1",
        "strategySpecRef": "spec:momentum-v2",
        "licenceClass": "restricted",
        "summary": "trend following over 200d",
    }


async def test_caller_supplied_score_refuses_before_any_write() -> None:
    """A score field on the record models is refused at validation; the
    derived score is engine-computed."""
    transport = InMemoryWriteBackTransport()
    transport.seed("ACME", "v1", {})
    _sink(transport)

    with pytest.raises(ValidationError, match="Extra inputs"):
        SentimentFusionRecord(
            subjectListing="ACME",
            components=[_component("newswire", -0.2, 1.0, "ev-4")],
            score=-0.4,
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        GeopoliticalRiskRecord(
            region="eu-west",
            components=[_component("wire", 0.7, 1.0, "ev-6")],
            score=0.5,
        )

    assert transport.attempts == 0


def test_component_bounds_refuse_before_any_write() -> None:
    """Component scores outside the record range and non-positive weights
    mirror the engine's stable refusal before any write."""
    transport = InMemoryWriteBackTransport()
    transport.seed("ACME", "v1", {})
    _sink(transport)

    with pytest.raises(ValidationError, match="SCORE_RANGE_INVALID"):
        SentimentFusionRecord(
            subjectListing="ACME",
            components=[_component("newswire", 1.5, 1.0, "ev-4")],
        )
    with pytest.raises(ValidationError, match="SCORE_RANGE_INVALID"):
        GeopoliticalRiskRecord(
            region="eu-west",
            components=[_component("wire", -0.1, 1.0, "ev-6")],
        )
    with pytest.raises(ValidationError, match="greater than"):
        SentimentFusionRecord(
            subjectListing="ACME",
            components=[_component("newswire", 0.1, 0.0, "ev-4")],
        )

    assert transport.attempts == 0


def test_closed_sets_refuse_unknown_values() -> None:
    """The medulla closed sets refuse unknown sides, bias kinds and
    licence classes at validation."""
    assert DEBATE_SIDES == ("bull", "bear")
    assert BIAS_KINDS == (
        "recency",
        "confirmation",
        "loss_aversion",
        "overconfidence",
    )
    assert LICENCE_CLASSES == ("open", "restricted", "proprietary")

    side: DebateSide = "bear"
    bias_kind: BiasKind = "confirmation"
    licence: LicenceClass = "open"
    assert side in DEBATE_SIDES and bias_kind in BIAS_KINDS
    assert licence in LICENCE_CLASSES

    with pytest.raises(ValidationError):
        DebateRoundRecord(
            debateId="debate-1",
            roundIndex=1,
            side="sideways",
            argument="a",
            evidenceRefs=["ev-1"],
        )
    with pytest.raises(ValidationError):
        BiasAudit(biasKind="hubris", finding="a", evidenceRefs=["ev-1"])
    with pytest.raises(ValidationError):
        StrategyCardRecord(
            cardId="card-1",
            strategySpecRef="spec:1",
            licenceClass="shared",
            summary="a",
        )


class _EngineRefused(Exception):
    """A fake engine refusal carrying the stable medulla code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class _RefusingEngine:
    """A fake engine that refuses the record write with a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.attempts = 0

    async def read_current(self, change_set: Any) -> SourceSnapshot:
        return SourceSnapshot(source_version="v1", fields={})

    async def preview(self, change_set: Any, current: SourceSnapshot) -> Any:
        raise AssertionError("refusing engine must not preview")

    async def prior_effect(self, change_set: Any) -> None:
        return None

    async def apply(self, change_set: Any, expected_version: str) -> Any:
        self.attempts += 1
        raise _EngineRefused(self.code, "a component cites no resolvable evidence node")

    async def reconcile(self, change_set: Any) -> Any:
        raise AssertionError("refusing engine must not reconcile")


async def test_engine_refusal_codes_pass_through() -> None:
    """A stable engine refusal code passes through the sink unchanged,
    and no effect is recorded."""
    engine = _RefusingEngine("SCORE_EVIDENCE_MISSING")
    sink = ResearchRecordSink(
        GovernedWriteBack(  # type: ignore[arg-type]
            engine,
            _ApprovingVerifier(),
            InMemoryAuditReservation(),
            clock_ms=lambda: FIXED_MS,
        ),
        tenant_id="tenant-1",
        actor="emerald:journal",
        connector_id="emerald-agent",
        source_instance_id="emerald-research",
        required_capability="finance.record.write",
        policy_digest="d" * 64,
        clock_ms=lambda: FIXED_MS,
    )
    record = SentimentFusionRecord(
        subjectListing="ACME",
        components=[_component("newswire", -0.2, 1.0, "ev-4")],
    )

    with pytest.raises(_EngineRefused) as caught:
        await sink.write_sentiment(
            record, authorization=_decision(), base_source_version="v1"
        )
    assert caught.value.code == "SCORE_EVIDENCE_MISSING"
    assert engine.attempts == 1
