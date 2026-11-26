"""Meeting transcript models (SCION-SOURCE-INGEST-R014.1)."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "FinalizedTranscriptReceipt",
    "MeetingSegment",
    "MeetingSummaryRequest",
    "SummaryProviderBinding",
    "text_digest",
]

Identity = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^\S(?:.*\S)?$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_TEXT_BYTES = 16_384


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def text_digest(text: str) -> str:
    """SHA-256 of the UTF-8 text as 64 lowercase hex characters."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class MeetingSegment(_Model):
    """One ordered transcript segment; its identity is (meeting, sequence, revision)."""

    tenant_id: Identity
    meeting_id: Identity
    segment_id: Identity
    sequence: int = Field(ge=0)
    revision: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_ref: Identity | None = None
    text: str = Field(max_length=MAX_TEXT_BYTES)
    text_digest: Digest

    @model_validator(mode="after")
    def bind(self) -> MeetingSegment:
        if len(self.text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError("segment text exceeds 16384 bytes")
        if self.end_ms < self.start_ms:
            raise ValueError("segment ends before it starts")
        if self.text_digest != text_digest(self.text):
            raise ValueError("text digest does not bind the text")
        return self

    @property
    def identity(self) -> tuple[str, int, int]:
        return (self.meeting_id, self.sequence, self.revision)


class FinalizedTranscriptReceipt(_Model):
    """The engine's seal over a complete contiguous transcript."""

    meeting_id: Identity
    finalization_digest: Digest
    ordered_segment_ids: tuple[Identity, ...] = Field(min_length=1, strict=False)
    final_revision: int = Field(ge=0)
    receipt_id: Identity
    finalized_at_ms: int = Field(ge=0)


class SummaryProviderBinding(_Model):
    """The admitted summary provider; a ``local`` binding never reaches hosted."""

    provider: Identity
    model: Identity
    execution_class: Literal["local", "hosted"]
    policy_revision: Identity
    admission_id: Identity
    admission_digest: Digest


class MeetingSummaryRequest(_Model):
    """One finalized receipt and one provider binding."""

    receipt: FinalizedTranscriptReceipt
    binding: SummaryProviderBinding
