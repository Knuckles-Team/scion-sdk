"""Attach and resolve visual observations (SCION-CONNECTOR-CONTROL-R052.2/.4/.5).

A tool returns its envelope in ``_meta["scion/observation"]``. A later step
passes the ``observation_id`` back and :func:`resolve_observation` accepts it
only when the reference is committed in the same run. The SDK keeps no durable
copy: commit state is read from the :class:`ObservationStore` the host binds.

A foreign tenant's or run's observation answers exactly like an absent one,
``observation_uncommitted``; no refusal confirms that it exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict

from scion_sdk.mcp.context import ToolExecutionContext
from scion_sdk.mcp.visual_observation import VisualObservationEnvelope

__all__ = [
    "OBSERVATION_META_KEY",
    "REFUSAL_CODES",
    "ObservationCommit",
    "ObservationEvidence",
    "ObservationRefused",
    "ObservationStore",
    "ObservedResult",
    "attach_observation",
    "resolve_observation",
]

OBSERVATION_META_KEY = "scion/observation"
SCHEMA_REVISION = "1"
REFUSAL_CODES = (
    "observation_expired",
    "observation_invalidated",
    "observation_uncommitted",
    "observation_unauthorized",
    "observation_scope_mismatch",
    "observation_schema_mismatch",
)


class ObservationRefused(RuntimeError):
    """A reference was not accepted; ``code`` is one of :data:`REFUSAL_CODES`."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ObservationCommit(BaseModel):
    """The host's committed record of one reference."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    envelope: VisualObservationEnvelope
    state: Literal["committed", "invalidated"]
    expires_at: AwareDatetime
    allowed_actor_ids: frozenset[str] = frozenset()
    retention: str = "run"


class ObservationEvidence(BaseModel):
    """What one resolution binds; excludes raw content and secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    content_digest: str
    source_operation_id: str
    frame: str
    tenant_id: str
    run_id: str
    schema_revision: str
    retention: str
    consumer_operation_id: str


@runtime_checkable
class ObservationStore(Protocol):
    """Reads committed references; the durable record is the host's."""

    def get(self, observation_id: str) -> ObservationCommit | None: ...


@dataclass(frozen=True)
class ObservedResult:
    """A tool result together with the observation it produced."""

    value: dict[str, Any]
    envelope: VisualObservationEnvelope


def attach_observation(
    result: dict[str, Any],
    envelope: VisualObservationEnvelope,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    """``result`` with the envelope in ``_meta``; tenant, run and request must match.

    The run is the context's ``session_id``.
    """
    if (
        envelope.tenant_id != context.tenant_id
        or envelope.run_id != context.session_id
        or envelope.request_id != context.request_id
        or envelope.source_operation_id != context.operation_id
    ):
        raise ObservationRefused("observation_scope_mismatch")
    meta = dict(result.get("_meta") or {})
    meta[OBSERVATION_META_KEY] = envelope.model_dump(mode="json")
    return {**result, "_meta": meta}


def resolve_observation(
    context: ToolExecutionContext,
    observation_id: str,
    store: ObservationStore,
    *,
    expected_modality: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[VisualObservationEnvelope, ObservationEvidence]:
    """The committed same-run envelope of ``observation_id``, or a refusal.

    No provider is called on any path.
    """
    record = store.get(observation_id)
    if (
        record is None
        or record.envelope.tenant_id != context.tenant_id
        or record.envelope.run_id != context.session_id
    ):
        raise ObservationRefused("observation_uncommitted")
    envelope = record.envelope
    if record.state == "invalidated":
        raise ObservationRefused("observation_invalidated")
    if record.expires_at <= clock():
        raise ObservationRefused("observation_expired")
    if record.allowed_actor_ids and context.actor_id not in record.allowed_actor_ids:
        raise ObservationRefused("observation_unauthorized")
    if envelope.schema_revision != SCHEMA_REVISION:
        raise ObservationRefused("observation_schema_mismatch")
    if expected_modality is not None and envelope.modality != expected_modality:
        raise ObservationRefused("observation_scope_mismatch")
    evidence = ObservationEvidence(
        content_digest=envelope.content_digest,
        source_operation_id=envelope.source_operation_id,
        frame=envelope.frame_id or str(envelope.sequence),
        tenant_id=envelope.tenant_id,
        run_id=envelope.run_id,
        schema_revision=envelope.schema_revision,
        retention=record.retention,
        consumer_operation_id=context.operation_id,
    )
    return envelope, evidence
