"""Paper retrieval stages papers; claims land only for papers the engine admits."""

from __future__ import annotations

import pytest
from medulla.generated.source_ingestion import (
    SourceIngestionDisposition,
    SourceIngestStatus,
)

from scion_sdk.adapters.paper_admission import (
    CLAIM_MAPPING,
    STAGED_MAPPING,
    AdmissionBinding,
    AdmissionResult,
    PaperAdmissionRefusal,
    StagedPaper,
    admit_staged_papers,
    stage_papers,
)
from scion_sdk.testing.sinks import _ingestion_receipt

BINDING = AdmissionBinding(
    graph="sdk-live",
    plan={"steps": []},
    feature_schema={"id": "papers"},
    policy={"id": "policy"},
)
PAPERS = (
    StagedPaper(
        paper_id="p1", summary="s1", content_digest="a" * 64, claim_ids=("c1",)
    ),
    StagedPaper(
        paper_id="p2", summary="s2", content_digest="b" * 64, claim_ids=("c2",)
    ),
)
CLAIMS = {"c1": {"text": "one"}, "c2": {"text": "two"}}


class _Sink:
    def __init__(self):
        self.batches = []

    async def source_status(self, connector, stream):
        return SourceIngestStatus(connector=connector, stream=stream)

    async def submit(self, batch):
        self.batches.append(batch)
        return _ingestion_receipt(
            batch,
            affected=len(batch.records),
            disposition=SourceIngestionDisposition.COMMITTED,
        )


class _Engine:
    def __init__(self, batch=None, error=None):
        self.batch, self.error, self.requests = batch, error, []

    async def decide(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.batch


def _advisory(admitted):
    return {
        "records": [
            {
                "record_digest": "d" * 8,
                "outcome": {
                    "outcome": "advisory",
                    "scores": [{"option_id": p} for p in admitted],
                },
                "premises": [
                    {"fact": f"paper.claim_ids:{p}={'c1' if p == 'p1' else 'c2'}"}
                    for p in admitted
                ],
            }
        ]
    }


async def _admit(sink, engine):
    return await admit_staged_papers(
        sink,
        engine,
        BINDING,
        connector="scholarx",
        claim_stream="claims",
        tenant="t",
        goals="goal",
        graph_version=7,
        claims=CLAIMS,
        staged=PAPERS,
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R904")
@pytest.mark.asyncio
async def test_claims_ingested_only_for_the_admitted_paper():
    sink = _Sink()
    await stage_papers(sink, "scholarx", "papers", PAPERS)
    staged = sink.batches[0].records
    assert {r.mapping_reference for r in staged} == {STAGED_MAPPING}
    assert not any("claim" in r.record_id for r in staged)

    engine = _Engine(_advisory(["p1"]))
    result = await _admit(sink, engine)
    request = engine.requests[0]
    assert request["question"]["kind"] == "paper_admission"
    assert request["candidates"]["source"] == "graph"
    params = {p["name"]: p["value"]["value"] for p in request["params"]}
    assert params == {"graph.version": 7, "platform.goals": "goal"}
    claim_batch = sink.batches[1]
    assert [r.record_id for r in claim_batch.records] == ["c1"]
    assert claim_batch.records[0].mapping_reference == CLAIM_MAPPING
    assert claim_batch.records[0].payload["admission_record"] == "d" * 8
    assert isinstance(result, AdmissionResult)
    assert result.admitted == ("p1",) and result.excluded == ("p2",)


@pytest.mark.spec("SCION-SOURCE-INGEST-R904")
@pytest.mark.asyncio
async def test_unavailable_or_refusing_engine_ingests_zero_claims():
    sink = _Sink()
    with pytest.raises(PaperAdmissionRefusal) as down:
        await _admit(sink, _Engine(error=ConnectionError("away")))
    assert down.value.code == "PAPER_ADMISSION_UNAVAILABLE"
    with pytest.raises(PaperAdmissionRefusal) as empty:
        await _admit(sink, _Engine(batch={"records": []}))
    assert empty.value.code == "PAPER_ADMISSION_UNAVAILABLE"
    for code in ("PAPER_SNAPSHOT_STALE", "PAPER_DUPLICATE"):
        abstained = {
            "records": [
                {"outcome": {"outcome": "abstained", "reasons": [{"code": code}]}}
            ]
        }
        with pytest.raises(PaperAdmissionRefusal) as refused:
            await _admit(sink, _Engine(batch=abstained))
        assert refused.value.code == code
    assert sink.batches == []


@pytest.mark.spec("SCION-SOURCE-INGEST-R904")
@pytest.mark.asyncio
async def test_no_admitted_paper_ingests_no_claim_batch():
    sink = _Sink()
    result = await _admit(sink, _Engine(_advisory([])))
    assert result.claim_receipt is None and sink.batches == []
    assert result.excluded == ("p1", "p2")
