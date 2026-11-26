"""The visual observation envelope (SCION-CONNECTOR-CONTROL-R052.1).

A connector tool that returns a visual observation returns this envelope; the
payload stays behind ``content_ref``. The envelope is bounded to 8 KiB.
"""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

__all__ = ["MAX_ENVELOPE_BYTES", "VisualObservationEnvelope"]

MAX_ENVELOPE_BYTES = 8192
_ID = r"^\S(?:.*\S)?$"


class VisualObservationEnvelope(BaseModel):
    """A reference to one observation, never its content."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    observation_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    modality: str = Field(min_length=1, max_length=64, pattern=_ID)
    source_operation_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    frame_id: str | None = Field(default=None, min_length=1, max_length=256)
    sequence: int | None = Field(default=None, ge=0)
    captured_at: AwareDatetime
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_ref: str = Field(min_length=1, max_length=1024)
    tenant_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    run_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    request_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    schema_revision: str = Field(min_length=1, max_length=64, pattern=_ID)

    @model_validator(mode="after")
    def _bounded(self) -> VisualObservationEnvelope:
        if self.frame_id is None and self.sequence is None:
            raise ValueError("an observation names a frame_id or a sequence")
        if len(self.model_dump_json().encode("utf-8")) > MAX_ENVELOPE_BYTES:
            raise ValueError("envelope exceeds 8 KiB")
        return self
