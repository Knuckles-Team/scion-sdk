"""The four meeting transcript tools on a connector server (SCION-SOURCE-INGEST-R014.3)."""

from __future__ import annotations

from typing import Any

from scion_sdk.meeting.client import MeetingTranscriptClient
from scion_sdk.meeting.models import FinalizedTranscriptReceipt, MeetingSegment

__all__ = ["MEETING_TOOLS", "register_meeting_tools"]

MEETING_TOOLS = (
    "meeting_append",
    "meeting_correct",
    "meeting_finalize",
    "meeting_get_final",
)


def register_meeting_tools(server: Any, client: MeetingTranscriptClient) -> None:
    """Register the transcript tools; single-shot dictation stays a separate path."""

    async def meeting_append(segments: list[MeetingSegment]) -> dict[str, int]:
        """Append one to 128 ordered partial transcript segments."""
        return {"accepted": await client.append(segments)}

    async def meeting_correct(segment: MeetingSegment) -> dict[str, int]:
        """Advance one segment to its next revision."""
        await client.correct(segment)
        return {"revision": segment.revision}

    async def meeting_finalize(
        meeting_id: str, ordered_segment_ids: list[str], final_revision: int
    ) -> FinalizedTranscriptReceipt:
        """Seal a complete contiguous transcript."""
        return await client.finalize(meeting_id, ordered_segment_ids, final_revision)

    async def meeting_get_final(
        meeting_id: str, ordered_segment_ids: list[str], final_revision: int
    ) -> FinalizedTranscriptReceipt:
        """Read the current seal of a transcript."""
        return await client.get_final(meeting_id, ordered_segment_ids, final_revision)

    server.tool(name="meeting_append")(meeting_append)
    server.tool(name="meeting_correct")(meeting_correct)
    server.tool(name="meeting_finalize")(meeting_finalize)
    server.tool(name="meeting_get_final", annotations={"readOnlyHint": True})(
        meeting_get_final
    )
