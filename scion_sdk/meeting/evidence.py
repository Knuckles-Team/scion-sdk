"""Meeting evidence: digests and counts only (SCION-SOURCE-INGEST-R014.5)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scion_sdk.meeting.models import (
    FinalizedTranscriptReceipt,
    MeetingSegment,
    MeetingSummaryRequest,
)

__all__ = ["meeting_evidence"]


def meeting_evidence(
    subject: MeetingSegment | FinalizedTranscriptReceipt | MeetingSummaryRequest,
    *,
    corrections: int = 0,
    invocation_receipt: str | None = None,
) -> dict[str, Any]:
    """Evidence binding meeting, segments, corrections and the final digest.

    Raw text, audio and credentials never appear; a segment contributes its digest.
    """
    if isinstance(subject, MeetingSegment):
        return {
            "meeting_id": subject.meeting_id,
            "tenant_id": subject.tenant_id,
            "segment_ids": [subject.segment_id],
            "segment_digest": subject.text_digest,
            "revision": subject.revision,
            "corrections": corrections,
        }
    if isinstance(subject, MeetingSummaryRequest):
        evidence = meeting_evidence(subject.receipt, corrections=corrections)
        binding = subject.binding
        evidence["provider"] = {
            "provider": binding.provider,
            "execution_class": binding.execution_class,
            "policy_revision": binding.policy_revision,
            "admission_digest": binding.admission_digest,
            "invocation_receipt": invocation_receipt,
        }
        return evidence
    ordered = json.dumps(subject.ordered_segment_ids, separators=(",", ":"))
    return {
        "meeting_id": subject.meeting_id,
        "segment_ids": list(subject.ordered_segment_ids),
        "segment_count": len(subject.ordered_segment_ids),
        "ordered_digest": hashlib.sha256(ordered.encode()).hexdigest(),
        "final_transcript_digest": subject.finalization_digest,
        "final_revision": subject.final_revision,
        "corrections": corrections,
    }
