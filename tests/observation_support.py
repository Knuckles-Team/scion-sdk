"""Shared builders for the visual observation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scion_sdk.mcp.context import ToolExecutionContext
from scion_sdk.mcp.tool_context import ObservationCommit
from scion_sdk.mcp.visual_observation import VisualObservationEnvelope

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def context(**changes: object) -> ToolExecutionContext:
    fields: dict[str, object] = {
        "tenant_id": "t1",
        "actor_id": "actor-1",
        "session_id": "run-1",
        "request_id": "req-1",
        "connector_id": "desktop",
        "operation_id": "capture",
        "deadline": NOW + timedelta(minutes=1),
    }
    return ToolExecutionContext(**{**fields, **changes})  # type: ignore[arg-type]


def envelope(**changes: object) -> VisualObservationEnvelope:
    fields: dict[str, object] = {
        "observation_id": "obs-1",
        "modality": "screenshot",
        "source_operation_id": "capture",
        "frame_id": "frame-1",
        "captured_at": NOW,
        "content_digest": "c" * 64,
        "content_ref": "blob://frames/1",
        "tenant_id": "t1",
        "run_id": "run-1",
        "request_id": "req-1",
        "schema_revision": "1",
    }
    return VisualObservationEnvelope(**{**fields, **changes})  # type: ignore[arg-type]


class Store:
    def __init__(self) -> None:
        self.records: dict[str, ObservationCommit] = {}
        self.reads = 0

    def commit(self, env: VisualObservationEnvelope, **changes: object) -> None:
        fields: dict[str, object] = {
            "envelope": env,
            "state": "committed",
            "expires_at": NOW + timedelta(hours=1),
        }
        self.records[env.observation_id] = ObservationCommit(**{**fields, **changes})  # type: ignore[arg-type]

    def get(self, observation_id: str) -> ObservationCommit | None:
        self.reads += 1
        return self.records.get(observation_id)
