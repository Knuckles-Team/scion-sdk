"""Paper retrieval stages papers; the engine admits them; only then claims land.

The engine decides which staged papers are admitted (``Decide`` with the
``paper_admission`` question kind). This module holds no relevance threshold and
no fallback admission: without an engine answer it ingests no claim at all.
Staged papers and admitted claims reach the engine only through ``SourceIngest``
(the :class:`~scion_sdk.ports.sink.Sink` port).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from medulla.generated.source_ingestion import (
    SourceCheckpoint,
    SourceIngestionMode,
    SourceIngestionReceipt,
    SourceIngestionRequest,
    SourceRecord,
    SourceRecordProvenance,
)
from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.decide.transport import DecideTransport
from scion_sdk.ports.sink import Sink

__all__ = [
    "CLAIM_MAPPING",
    "STAGED_MAPPING",
    "AdmissionBinding",
    "AdmissionResult",
    "PaperAdmissionRefusal",
    "StagedPaper",
    "admit_staged_papers",
    "stage_papers",
]

STAGED_MAPPING = "StagedPaper"
CLAIM_MAPPING = "PaperClaim"
_PASS_THROUGH = frozenset({"PAPER_SNAPSHOT_STALE", "PAPER_DUPLICATE"})
_UNAVAILABLE = "PAPER_ADMISSION_UNAVAILABLE"


class PaperAdmissionRefusal(RuntimeError):
    """A typed refusal; ``code`` is the closed code a caller branches on."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class StagedPaper(BaseModel):
    """One fetched paper as the engine reads it: summary, digest and claim ids."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    paper_id: str = Field(min_length=1)
    summary: str
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AdmissionBinding:
    """The operator-published ``Decide`` binding of the paper-admission question."""

    graph: str
    plan: Mapping[str, Any]
    feature_schema: Mapping[str, Any]
    policy: Mapping[str, Any]
    head: Mapping[str, Any] | None = None
    question_id: str = "paper.admission"


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """What one admission pass committed."""

    admitted: tuple[str, ...]
    excluded: tuple[str, ...]
    decision_digest: str | None
    claim_receipt: SourceIngestionReceipt | None


def _digest(parts: Sequence[object]) -> str:
    body = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _record(
    connector: str, stream: str, mapping: str, record_id: str, payload: dict[str, Any]
) -> SourceRecord:
    return SourceRecord(
        stream=stream,
        record_id=record_id,
        mapping_reference=mapping,
        payload=payload,
        provenance=SourceRecordProvenance(
            connector=connector,
            adapter_kind="paper_admission",
            server=connector,
            tool=mapping,
            tool_schema_sha256=_digest([mapping]),
            source_uri=f"paper://{connector}/{stream}/{record_id}",
        ),
    )


async def _commit(
    sink: Sink, connector: str, stream: str, records: list[SourceRecord]
) -> SourceIngestionReceipt:
    status = await sink.source_status(connector, stream)
    digest = _digest([r.model_dump(mode="json") for r in records])
    request = SourceIngestionRequest(
        connector=connector,
        mode=SourceIngestionMode.DELTA,
        provider_checkpoint=SourceCheckpoint(
            stream=stream, position=f"paper-admission:{digest}", content_hash=digest
        ),
        expected_previous_checkpoint=status.accepted_checkpoint,
        records=records,
        strict_schema=True,
    )
    return await sink.submit(request)


async def stage_papers(
    sink: Sink, connector: str, stream: str, papers: Sequence[StagedPaper]
) -> SourceIngestionReceipt:
    """Commit each fetched paper as a staged node; no claim is written here."""
    records = [
        _record(
            connector,
            stream,
            STAGED_MAPPING,
            paper.paper_id,
            {
                "paper.summary": paper.summary,
                "paper.content_digest": paper.content_digest,
                "paper.claim_ids": ",".join(paper.claim_ids),
            },
        )
        for paper in papers
    ]
    return await _commit(sink, connector, stream, records)


def _request(
    binding: AdmissionBinding, tenant: str, goals: str, graph_version: int
) -> dict[str, Any]:
    return {
        "tenant_id": tenant,
        "question": {
            "question_id": binding.question_id,
            "kind": "paper_admission",
            "safety": "advisory",
        },
        "candidates": {
            "source": "graph",
            "graph": binding.graph,
            "plan": dict(binding.plan),
        },
        "feature_schema": dict(binding.feature_schema),
        "head": None if binding.head is None else dict(binding.head),
        "policy": dict(binding.policy),
        "params": [
            {"name": "graph.version", "value": {"type": "int", "value": graph_version}},
            {"name": "platform.goals", "value": {"type": "text", "value": goals}},
        ],
        "max_records": 1,
    }


def _record_of(batch: Any) -> Mapping[str, Any]:
    records = batch.get("records") if isinstance(batch, Mapping) else None
    if not records or not isinstance(records[0], Mapping):
        raise PaperAdmissionRefusal(_UNAVAILABLE)
    return records[0]


def _abstain_code(outcome: Mapping[str, Any]) -> str:
    for reason in outcome.get("reasons") or []:
        code = reason.get("code") if isinstance(reason, Mapping) else None
        if code in _PASS_THROUGH:
            return str(code)
    return _UNAVAILABLE


def _admitted(record: Mapping[str, Any]) -> tuple[str, ...]:
    outcome = record.get("outcome")
    if not isinstance(outcome, Mapping) or outcome.get("outcome") != "advisory":
        raise PaperAdmissionRefusal(
            _abstain_code(outcome) if isinstance(outcome, Mapping) else _UNAVAILABLE
        )
    scores = outcome.get("scores") or []
    return tuple(
        str(s["option_id"])
        for s in scores
        if isinstance(s, Mapping) and "option_id" in s
    )


def _engine_claims(record: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Claim ids per admitted paper, read from the engine's own premises."""
    found: dict[str, tuple[str, ...]] = {}
    prefix = "paper.claim_ids:"
    for premise in record.get("premises") or []:
        fact = premise.get("fact") if isinstance(premise, Mapping) else None
        if isinstance(fact, str) and fact.startswith(prefix):
            paper, _, claims = fact.removeprefix(prefix).partition("=")
            found[paper] = tuple(c for c in claims.split(",") if c)
    return found


async def admit_staged_papers(
    sink: Sink,
    transport: DecideTransport,
    binding: AdmissionBinding,
    *,
    connector: str,
    claim_stream: str,
    tenant: str,
    goals: str,
    graph_version: int,
    claims: Mapping[str, Mapping[str, Any]],
    staged: Sequence[StagedPaper],
) -> AdmissionResult:
    """Ask the engine which staged papers to admit; ingest claims for those only.

    Raises:
        PaperAdmissionRefusal: ``PAPER_ADMISSION_UNAVAILABLE`` without an engine
            answer (nothing ingested), or the engine's own ``PAPER_SNAPSHOT_STALE``
            and ``PAPER_DUPLICATE`` unchanged.
    """
    try:
        batch = await transport.decide(_request(binding, tenant, goals, graph_version))
    except Exception as error:
        raise PaperAdmissionRefusal(_UNAVAILABLE) from error
    record = _record_of(batch)
    admitted = _admitted(record)
    digest = str(record.get("record_digest") or "")
    selected = _engine_claims(record)
    excluded = tuple(p.paper_id for p in staged if p.paper_id not in admitted)
    records = [
        _record(
            connector,
            claim_stream,
            CLAIM_MAPPING,
            claim_id,
            {**dict(claims[claim_id]), "paper_id": paper, "admission_record": digest},
        )
        for paper in admitted
        for claim_id in selected.get(paper, ())
        if claim_id in claims
    ]
    receipt = await _commit(sink, connector, claim_stream, records) if records else None
    return AdmissionResult(admitted, excluded, digest or None, receipt)
