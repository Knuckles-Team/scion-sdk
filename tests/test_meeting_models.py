"""Meeting transcript models are closed and validate identity and bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scion_sdk.meeting import (
    FinalizedTranscriptReceipt,
    MeetingSegment,
    MeetingSummaryRequest,
    SummaryProviderBinding,
    text_digest,
)

DIGEST = "b" * 64


def segment(**changes):
    values = {
        "tenant_id": "t",
        "meeting_id": "m",
        "segment_id": "s0",
        "sequence": 0,
        "revision": 1,
        "start_ms": 0,
        "end_ms": 10,
        "text": "hello",
        "text_digest": text_digest("hello"),
    }
    return MeetingSegment(**(values | changes))


@pytest.mark.spec("SCION-SOURCE-INGEST-R014.1")
def test_models_are_closed_and_validate_identity_and_bounds():
    one = segment()
    assert MeetingSegment.model_validate(one.model_dump()) == one
    assert one.identity == ("m", 0, 1)
    assert len(one.text_digest) == 64
    for bad in (
        {"meeting_id": ""},
        {"end_ms": 5, "start_ms": 6},
        {"revision": 0},
        {"sequence": -1},
        {"text": "x" * 16_385, "text_digest": text_digest("x" * 16_385)},
        {"text_digest": DIGEST},
    ):
        with pytest.raises(ValidationError):
            segment(**bad)
    with pytest.raises(ValidationError):
        MeetingSegment(**{**one.model_dump(), "extra": 1})
    multibyte = "é" * 8_193  # 16386 bytes, within the character bound
    with pytest.raises(ValidationError, match="16384 bytes"):
        segment(text=multibyte, text_digest=text_digest(multibyte))
    receipt = FinalizedTranscriptReceipt(
        meeting_id="m",
        finalization_digest=DIGEST,
        ordered_segment_ids=("s0",),
        final_revision=1,
        receipt_id="r",
        finalized_at_ms=5,
    )
    binding = SummaryProviderBinding(
        provider="p",
        model="x",
        execution_class="local",
        policy_revision="v1",
        admission_id="a",
        admission_digest=DIGEST,
    )
    assert MeetingSummaryRequest(receipt=receipt, binding=binding).binding == binding
    with pytest.raises(ValidationError):
        SummaryProviderBinding(**{**binding.model_dump(), "execution_class": "cloud"})
    with pytest.raises(ValidationError):
        FinalizedTranscriptReceipt(
            **{**receipt.model_dump(), "ordered_segment_ids": ()}
        )
