"""Meeting transcript transport to the engine (SCION-SOURCE-INGEST-R014.2).

One engine method, ``MeetingTranscriptRevision``, carries every operation. The pinned
engine client may lack it, so every call goes through one capability guard that
raises ``EngineCapabilityUnavailable`` naming the method.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from scion_sdk.engine_capability import require_engine_capability
from scion_sdk.meeting.errors import ENGINE_CODES, MeetingRefusal
from scion_sdk.meeting.models import (
    FinalizedTranscriptReceipt,
    MeetingSegment,
    MeetingSummaryRequest,
    SummaryProviderBinding,
)

__all__ = [
    "MeetingEngine",
    "MeetingTranscriptClient",
    "require_execution_class",
    "require_finalized",
]

METHOD = "MeetingTranscriptRevision"
MAX_APPEND = 128


class MeetingEngine(Protocol):
    """The engine client surface the transcript client calls."""

    async def meeting_transcript_revision(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class MeetingTranscriptClient:
    """Appends, corrects and finalizes transcripts; the engine owns order and conflict."""

    def __init__(
        self, engine: MeetingEngine, *, clock_ms: Callable[[], int], provenance_ref: str
    ) -> None:
        self._engine = engine
        self._clock_ms = clock_ms
        self._provenance = provenance_ref
        self._sequence = 0

    async def _send(self, request: dict[str, Any]) -> Mapping[str, Any]:
        require_engine_capability(METHOD)
        self._sequence += 1
        request = {**request, "monotonic_event_sequence": self._sequence}
        try:
            return await self._engine.meeting_transcript_revision(request)
        except Exception as error:
            code = getattr(error, "code", None)
            if code in ENGINE_CODES:
                raise MeetingRefusal(code) from None
            raise

    def _segment_request(self, segment: MeetingSegment, op: str) -> dict[str, Any]:
        return {
            "op": op,
            "meeting_id": segment.meeting_id,
            "base_revision": segment.revision - 1,
            "new_revision": segment.revision,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "speaker_ref": segment.speaker_ref,
            "text_digest": segment.text_digest,
            "ordered_segment_ids": [segment.segment_id],
            "provenance_ref": self._provenance,
            "idempotency_key": (
                f"{segment.meeting_id}:{segment.sequence}:{segment.revision}:"
                f"{segment.text_digest}"
            ),
        }

    async def append(self, segments: Sequence[MeetingSegment]) -> int:
        """Send one to 128 partial segments; return how many the engine accepted."""
        if not 1 <= len(segments) <= MAX_APPEND:
            raise MeetingRefusal("SEGMENT_INVALID")
        for segment in segments:
            await self._send(self._segment_request(segment, "append"))
        return len(segments)

    async def correct(self, segment: MeetingSegment) -> None:
        """Advance one segment to its next revision."""
        if segment.revision < 2:
            raise MeetingRefusal("SEGMENT_INVALID")
        await self._send(self._segment_request(segment, "correct"))

    async def finalize(
        self, meeting_id: str, ordered_segment_ids: Sequence[str], final_revision: int
    ) -> FinalizedTranscriptReceipt:
        """Seal the transcript; the engine refuses a gap or a stale segment."""
        if not ordered_segment_ids:
            raise MeetingRefusal("SEGMENT_INVALID")
        key = hashlib.sha256(
            f"{meeting_id}:{final_revision}:{'|'.join(ordered_segment_ids)}".encode()
        ).hexdigest()
        answer = await self._send(
            {
                "op": "finalize",
                "meeting_id": meeting_id,
                "base_revision": final_revision,
                "new_revision": final_revision,
                "start_ms": 0,
                "end_ms": 0,
                "text_digest": key,
                "ordered_segment_ids": list(ordered_segment_ids),
                "provenance_ref": self._provenance,
                "idempotency_key": f"finalize:{key}",
            }
        )
        digest = answer.get("finalization_digest")
        if digest is None:
            raise MeetingRefusal("TRANSCRIPT_NOT_FINAL")
        return FinalizedTranscriptReceipt(
            meeting_id=meeting_id,
            finalization_digest=digest,
            ordered_segment_ids=tuple(ordered_segment_ids),
            final_revision=int(answer["revision"]),
            receipt_id=f"transcript-receipt:{digest}",
            finalized_at_ms=self._clock_ms(),
        )

    async def get_final(
        self, meeting_id: str, ordered_segment_ids: Sequence[str], final_revision: int
    ) -> FinalizedTranscriptReceipt:
        """The current seal, read back by replaying the idempotent finalize."""
        return await self.finalize(meeting_id, ordered_segment_ids, final_revision)


def require_finalized(
    request: MeetingSummaryRequest, current: FinalizedTranscriptReceipt
) -> None:
    """Refuse a summary unless its receipt is the engine's current seal.

    Raises:
        MeetingRefusal: ``SUMMARY_BEFORE_FINALIZE`` when the digests differ, and
            ``PROVIDER_CLASS_MISMATCH`` never falls through to another class.
    """
    if request.receipt.finalization_digest != current.finalization_digest:
        raise MeetingRefusal("SUMMARY_BEFORE_FINALIZE")


def require_execution_class(binding: SummaryProviderBinding, executed: str) -> None:
    """A ``local`` binding never reaches hosted execution; there is no fall-through."""
    if binding.execution_class != executed:
        raise MeetingRefusal("PROVIDER_CLASS_MISMATCH")
