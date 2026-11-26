"""Component health classification, without asserting host integration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scion_sdk.connector_lifecycle import LifecycleState
from scion_sdk.host import ComponentHealthState, should_alert


def _health(**changes: object) -> ComponentHealthState:
    values = {
        "component_id": "worker",
        "lifecycle_state": LifecycleState.READY,
        "configuration_status": "initialized",
        "enabled": True,
        "last_check_time": datetime(2026, 10, 27, tzinfo=UTC),
    }
    values.update(changes)
    return ComponentHealthState.model_validate(values)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065.1")
def test_health_model_rejects_invalid_and_contradictory_states() -> None:
    observation = _health()
    assert (
        ComponentHealthState.model_validate_json(observation.model_dump_json())
        == observation
    )
    with pytest.raises(ValidationError):
        observation.lifecycle_state = LifecycleState.DEGRADED  # type: ignore[misc]
    for changes in (
        {"lifecycle_state": "unknown"},
        {"configuration_status": "unknown"},
        {"lifecycle_state": LifecycleState.UNCONFIGURED},
        {
            "configuration_status": "disabled",
            "enabled": False,
            "lifecycle_state": LifecycleState.BACKOFF,
            "alert_suppression_reason": "disabled",
        },
        {
            "configuration_status": "disabled",
            "enabled": False,
            "lifecycle_state": LifecycleState.UNCONFIGURED,
        },
        {"component_id": ""},
        {"last_check_time": datetime(2026, 10, 27)},
    ):
        with pytest.raises(ValidationError):
            _health(**changes)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065.2")
def test_alerts_only_for_enabled_initialized_degraded_or_unavailable() -> None:
    assert not should_alert(_health())
    assert should_alert(_health(lifecycle_state=LifecycleState.DEGRADED))
    assert should_alert(_health(lifecycle_state=LifecycleState.BACKOFF))
    assert not should_alert(
        _health(
            lifecycle_state=LifecycleState.UNCONFIGURED,
            configuration_status="not_initialized",
            enabled=True,
        )
    )
    assert not should_alert(
        _health(
            lifecycle_state=LifecycleState.UNCONFIGURED,
            configuration_status="disabled",
            enabled=False,
            alert_suppression_reason="disabled",
        )
    )
