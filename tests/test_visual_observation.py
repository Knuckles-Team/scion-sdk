"""The visual observation envelope and its same-run resolution (R052.1, .2, .4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from observation_support import NOW, Store, context, envelope
from pydantic import ValidationError

from scion_sdk.mcp.tool_context import (
    OBSERVATION_META_KEY,
    REFUSAL_CODES,
    ObservationEvidence,
    ObservationRefused,
    ObservationStore,
    attach_observation,
    resolve_observation,
)
from scion_sdk.mcp.visual_observation import (
    MAX_ENVELOPE_BYTES,
    VisualObservationEnvelope,
)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052.1")
def test_envelope_round_trip_and_bounds() -> None:
    env = envelope()
    assert VisualObservationEnvelope.model_validate_json(env.model_dump_json()) == env
    assert envelope(frame_id=None, sequence=3).sequence == 3
    for bad in (
        {"content_digest": "ABC"},
        {"frame_id": None},
        {"observation_id": " "},
        {"content_ref": "x" * (MAX_ENVELOPE_BYTES + 1)},
    ):
        with pytest.raises(ValidationError):
            envelope(**bad)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052.2")
def test_resolve_accepts_only_committed_same_run() -> None:
    env, ctx = envelope(), context()
    attached = attach_observation({"ok": True}, env, ctx)
    assert attached["_meta"][OBSERVATION_META_KEY]["observation_id"] == "obs-1"
    with pytest.raises(ObservationRefused):
        attach_observation({}, env, context(session_id="run-2"))
    store = Store()
    with pytest.raises(ObservationRefused) as caught:
        resolve_observation(ctx, "obs-1", store, clock=lambda: NOW)
    assert caught.value.code == "observation_uncommitted"
    store.commit(env)
    later = context(operation_id="read-frame", request_id="req-2")
    found, evidence = resolve_observation(later, "obs-1", store, clock=lambda: NOW)
    assert found == env and evidence.consumer_operation_id == "read-frame"
    assert "content_ref" not in evidence.model_dump()
    assert isinstance(evidence, ObservationEvidence) and isinstance(
        store, ObservationStore
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R052.4")
def test_refusals_make_zero_provider_calls() -> None:
    env, store = envelope(), Store()
    cases = {
        "observation_expired": dict(expires_at=NOW - timedelta(seconds=1)),
        "observation_invalidated": dict(state="invalidated"),
        "observation_unauthorized": dict(allowed_actor_ids=frozenset({"other"})),
    }
    for code, changes in cases.items():
        store.commit(env, **changes)
        with pytest.raises(ObservationRefused) as caught:
            resolve_observation(context(), "obs-1", store, clock=lambda: NOW)
        assert caught.value.code == code
    store.commit(env)
    for foreign in (context(session_id="run-2"), context(tenant_id="t2")):
        with pytest.raises(ObservationRefused) as caught:
            resolve_observation(foreign, "obs-1", store, clock=lambda: NOW)
        assert caught.value.code == "observation_uncommitted"  # no existence oracle
    with pytest.raises(ObservationRefused) as caught:
        resolve_observation(
            context(), "obs-1", store, expected_modality="audio", clock=lambda: NOW
        )
    assert caught.value.code == "observation_scope_mismatch"
    store.commit(envelope(schema_revision="9"))
    with pytest.raises(ObservationRefused) as caught:
        resolve_observation(context(), "obs-1", store, clock=lambda: NOW)
    assert caught.value.code == "observation_schema_mismatch"
    assert set(REFUSAL_CODES) >= {"observation_schema_mismatch"}
