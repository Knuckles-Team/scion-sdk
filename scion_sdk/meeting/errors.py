"""Closed meeting-transcript refusals (SCION-SOURCE-INGEST-R014.4)."""

from __future__ import annotations

__all__ = ["ENGINE_CODES", "SDK_CODES", "MeetingRefusal"]

SDK_CODES = frozenset(
    {"SUMMARY_BEFORE_FINALIZE", "PROVIDER_CLASS_MISMATCH", "SEGMENT_INVALID"}
)
#: Raised by the engine; the SDK passes them through unchanged.
ENGINE_CODES = frozenset(
    {"SEGMENT_CONFLICT", "SEGMENT_STALE", "TRANSCRIPT_GAP", "TRANSCRIPT_NOT_FINAL"}
)


class MeetingRefusal(RuntimeError):
    """A meeting refusal; ``code`` is an SDK code or a passed-through engine code."""

    def __init__(self, code: str) -> None:
        if code not in SDK_CODES | ENGINE_CODES:
            raise ValueError(f"unknown meeting refusal code: {code}")
        self.code = code
        super().__init__(code)
