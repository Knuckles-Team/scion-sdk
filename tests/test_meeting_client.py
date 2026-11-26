"""The meeting transcript client over a structural engine double and the pinned guard."""

from __future__ import annotations

import pytest

from scion_sdk.engine_capability import (
    ENGINE_CAPABILITIES,
    EngineCapabilityUnavailable,
    engine_capability_present,
)
from scion_sdk.meeting import (
    MeetingRefusal,
    MeetingSegment,
    MeetingSummaryRequest,
    MeetingTranscriptClient,
    SummaryProviderBinding,
    meeting_evidence,
    require_finalized,
    text_digest,
)
from scion_sdk.meeting.client import MeetingEngine, require_execution_class
from scion_sdk.meeting.errors import ENGINE_CODES, SDK_CODES

D1, D2 = "c" * 64, "d" * 64


def seg(sequence=0, revision=1, text="hello"):
    return MeetingSegment(
        tenant_id="t",
        meeting_id="m",
        segment_id=f"s{sequence}",
        sequence=sequence,
        revision=revision,
        start_ms=sequence * 10,
        end_ms=sequence * 10 + 5,
        text=text,
        text_digest=text_digest(text),
    )


class _Refusal(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _Engine:
    """Structural double of the engine's MeetingTranscriptRevision method."""

    def __init__(self):
        self.requests = []
        self.seen = {}
        self.segments = {}
        self.digest = D1

    async def meeting_transcript_revision(self, request):
        key = request["idempotency_key"]
        if key in self.seen:
            self.requests.append(request)
            return {**self.seen[key], "replayed": True}
        op = request["op"]
        sid = request["ordered_segment_ids"][0]
        if op in {"append", "correct"}:
            held = self.segments.get(sid)
            if op == "append" and held and held[0] == request["new_revision"]:
                raise _Refusal("SEGMENT_CONFLICT")
            if op == "correct" and (not held or held[0] >= request["new_revision"]):
                raise _Refusal("SEGMENT_STALE")
            self.segments[sid] = (request["new_revision"], request["text_digest"])
        elif op == "finalize" and set(request["ordered_segment_ids"]) - set(
            self.segments
        ):
            raise _Refusal("TRANSCRIPT_GAP")
        self.requests.append(request)
        answer = {
            "meeting_id": request["meeting_id"],
            "revision": request["new_revision"],
            "replayed": False,
            "finalization_digest": self.digest if op == "finalize" else None,
        }
        self.seen[key] = answer
        return answer


@pytest.fixture
def pinned_serves_the_method(monkeypatch):
    """Run the real path now: stand in for the pin that will carry the method."""
    monkeypatch.setitem(
        ENGINE_CAPABILITIES, "MeetingTranscriptRevision", ("builtins", "object")
    )


def client(engine: MeetingEngine):
    return MeetingTranscriptClient(engine, clock_ms=lambda: 1_000, provenance_ref="p")


@pytest.mark.spec("SCION-SOURCE-INGEST-R014.2")
async def test_replay_conflict_correction_and_finalize(pinned_serves_the_method):
    engine = _Engine()
    meeting = client(engine)
    assert await meeting.append([seg(0), seg(1)]) == 2
    before = len(engine.seen)
    assert await meeting.append([seg(0)]) == 1  # exact replay: no new effect
    assert len(engine.seen) == before
    with pytest.raises(MeetingRefusal) as conflict:
        await meeting.append([seg(0, text="different")])
    assert conflict.value.code == "SEGMENT_CONFLICT"
    await meeting.correct(seg(0, 2, "fixed"))
    with pytest.raises(MeetingRefusal) as stale:
        await meeting.correct(seg(0, 2, "fixed again"))
    assert stale.value.code == "SEGMENT_STALE"
    sequences = [r["monotonic_event_sequence"] for r in engine.requests]
    assert sequences == sorted(sequences)
    with pytest.raises(MeetingRefusal) as gap:
        await meeting.finalize("m", ["s0", "s1", "s2"], 2)
    assert gap.value.code == "TRANSCRIPT_GAP"
    receipt = await meeting.finalize("m", ["s0", "s1"], 2)
    assert receipt.finalization_digest == D1 and receipt.finalized_at_ms == 1_000
    assert await meeting.get_final("m", ["s0", "s1"], 2) == receipt


@pytest.mark.spec("SCION-SOURCE-INGEST-R014.4")
async def test_refusal_codes_precede_effects(pinned_serves_the_method):
    engine = _Engine()
    meeting = client(engine)
    with pytest.raises(MeetingRefusal) as empty:
        await meeting.append([])
    assert empty.value.code == "SEGMENT_INVALID"
    with pytest.raises(MeetingRefusal):
        await meeting.append([seg(i) for i in range(129)])
    with pytest.raises(MeetingRefusal):
        await meeting.correct(seg(0, 1))
    assert engine.requests == []  # every SDK refusal precedes the engine write
    await meeting.append([seg(0)])
    receipt = await meeting.finalize("m", ["s0"], 1)
    binding = SummaryProviderBinding(
        provider="p",
        model="x",
        execution_class="local",
        policy_revision="v1",
        admission_id="a",
        admission_digest=D2,
    )
    request = MeetingSummaryRequest(receipt=receipt, binding=binding)
    require_finalized(request, receipt)
    engine.digest = D2  # a correction moved the seal
    current = await client(_SealedAs(D2)).finalize("m", ["s0"], 1)
    with pytest.raises(MeetingRefusal) as early:
        require_finalized(request, current)
    assert early.value.code == "SUMMARY_BEFORE_FINALIZE"
    with pytest.raises(MeetingRefusal) as mismatch:
        require_execution_class(binding, "hosted")
    assert mismatch.value.code == "PROVIDER_CLASS_MISMATCH"
    require_execution_class(binding, "local")
    for engine_code in sorted(ENGINE_CODES):
        assert MeetingRefusal(engine_code).code == engine_code
    assert {
        "SUMMARY_BEFORE_FINALIZE",
        "PROVIDER_CLASS_MISMATCH",
        "SEGMENT_INVALID",
    } == SDK_CODES
    with pytest.raises(ValueError, match="unknown meeting refusal"):
        MeetingRefusal("CAPTURE_DENIED")  # arbora's code, never the SDK's


class _SealedAs:
    def __init__(self, digest):
        self.digest = digest

    async def meeting_transcript_revision(self, request):
        return {
            "meeting_id": request["meeting_id"],
            "revision": request["new_revision"],
            "replayed": False,
            "finalization_digest": self.digest,
        }


@pytest.mark.spec("SCION-SOURCE-INGEST-R014.5")
async def test_evidence_has_digests_only_and_matches_engine_readback(
    pinned_serves_the_method,
):
    engine = _Engine()
    meeting = client(engine)
    await meeting.append([seg(0, text="private words")])
    receipt = await meeting.finalize("m", ["s0"], 1)
    evidence = meeting_evidence(receipt, corrections=1)
    assert evidence["final_transcript_digest"] == engine.digest
    assert evidence["segment_ids"] == ["s0"] and evidence["corrections"] == 1
    segment_evidence = meeting_evidence(seg(0, text="private words"))
    assert segment_evidence["segment_digest"] == text_digest("private words")
    assert "private words" not in str(evidence) + str(segment_evidence)
    summary = MeetingSummaryRequest(
        receipt=receipt,
        binding=SummaryProviderBinding(
            provider="p",
            model="x",
            execution_class="hosted",
            policy_revision="v1",
            admission_id="a",
            admission_digest=D2,
        ),
    )
    bound = meeting_evidence(summary, invocation_receipt="inv-1")
    assert bound["provider"]["invocation_receipt"] == "inv-1"
    assert bound["final_transcript_digest"] == receipt.finalization_digest


@pytest.mark.spec("SCION-SOURCE-INGEST-R014.2")
async def test_pinned_engine_refuses_while_it_lacks_the_method():
    if engine_capability_present("MeetingTranscriptRevision"):
        pytest.skip("the pin serves MeetingTranscriptRevision")
    engine = _Engine()
    with pytest.raises(EngineCapabilityUnavailable) as caught:
        await client(engine).append([seg(0)])
    assert caught.value.method == "MeetingTranscriptRevision"
    assert engine.requests == []
