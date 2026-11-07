"""The runner feeds one lifecycle function (SCION-CONNECTOR-CONTROL-R917.3)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fleet_fixtures import (
    FakeFreshRss,
    build_enumerated_freshrss_server,
    freshrss_items,
)
from runner_support import (
    ListRegistry,
    RecordingSink,
    freshrss_descriptor,
    in_process,
    services,
)

from scion_sdk.connector_lifecycle import LifecycleState
from scion_sdk.credentials.resolver import EnvironmentCredentialResolver
from scion_sdk.runner.descriptors import EndpointSpec
from scion_sdk.runner.endpoints import CredentialEndpoints
from scion_sdk.runner.health_state import ConnectorHealth, RunnerHealth
from scion_sdk.runner.supervisor import ConnectorSyncRunner
from scion_sdk.testing.sinks import InMemorySink

pytestmark = [pytest.mark.spec("SCION-CONNECTOR-CONTROL-R917.3")]


def _health() -> RunnerHealth:
    return RunnerHealth(
        sink=InMemorySink(), liveness_window_seconds=30.0, degraded_failures=2
    )


async def _state(health: RunnerHealth) -> dict[str, object]:
    return (await health.readiness()).body["connectors"]["freshrss-agent"]  # type: ignore[no-any-return,index]


async def test_supervisor_events_drive_the_single_state_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    field_names = {field.name for field in dataclasses.fields(ConnectorHealth)}
    assert "lifecycle_state" in field_names
    assert "credentials_ok" not in field_names

    health = _health()
    server = build_enumerated_freshrss_server(FakeFreshRss(freshrss_items(0, 3)))
    registry = ListRegistry(freshrss_descriptor())
    built = services(
        RecordingSink(), in_process({"freshrss-agent": server}), health=health
    )
    assert await ConnectorSyncRunner(registry, built).run_once() == {
        "freshrss-agent": True
    }
    detail = await _state(health)
    assert detail["lifecycle_state"] == "Ready"
    assert detail["consecutive_failures"] == 0

    monkeypatch.delenv("LIFECYCLE_WIRING_TOKEN", raising=False)
    broken = _health()
    endpoint = EndpointSpec(
        url="https://freshrss.example.invalid/mcp",
        bearer_token="env://LIFECYCLE_WIRING_TOKEN",
    )
    runner = ConnectorSyncRunner(
        ListRegistry(freshrss_descriptor(endpoint=endpoint)),
        services(
            InMemorySink(),
            CredentialEndpoints(EnvironmentCredentialResolver()),
            health=broken,
        ),
    )
    assert await runner.run_once() == {"freshrss-agent": False}
    assert (await _state(broken))["lifecycle_state"] == "CredentialsPending"


async def test_creds_ok_clears_the_error_and_keeps_failures() -> None:
    health = _health()
    health.sync_registry(("freshrss-agent",))
    health.record_credentials("freshrss-agent", ok=False, error="denied")
    detail = await _state(health)
    assert (detail["lifecycle_state"], detail["credential_error"]) == (
        "CredentialsPending",
        "denied",
    )

    health.record_credentials("freshrss-agent", ok=True)
    detail = await _state(health)
    assert (detail["lifecycle_state"], detail["credential_error"]) == ("Warming", "")

    health.record_cycle_failure("freshrss-agent", next_retry_seconds=1.0)
    health.record_cycle_failure("freshrss-agent", next_retry_seconds=2.0)
    assert (await _state(health))["lifecycle_state"] == "Backoff"

    health.record_credentials("freshrss-agent", ok=True)
    detail = await _state(health)
    assert (detail["lifecycle_state"], detail["consecutive_failures"]) == ("Backoff", 2)

    health.record_cycle_success("freshrss-agent")
    detail = await _state(health)
    assert (detail["lifecycle_state"], detail["consecutive_failures"]) == ("Ready", 0)


def test_a_refused_event_keeps_the_record_unchanged() -> None:
    health = _health()
    health.sync_registry(("freshrss-agent",))
    health.record_cycle_failure("freshrss-agent", next_retry_seconds=1.0)
    stored = health._connectors["freshrss-agent"]
    assert stored.lifecycle_state is LifecycleState.CREDENTIALS_PENDING
    assert stored.consecutive_failures == 0


def test_a_removed_connector_stops_and_a_returned_one_starts_fresh() -> None:
    health = _health()
    health.sync_registry(("freshrss-agent",))
    health.sync_registry(())
    assert (
        health._connectors["freshrss-agent"].lifecycle_state is LifecycleState.STOPPED
    )
    health.sync_registry(("freshrss-agent",))
    assert (
        health._connectors["freshrss-agent"].lifecycle_state
        is LifecycleState.CREDENTIALS_PENDING
    )
