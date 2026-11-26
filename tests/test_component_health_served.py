"""Readiness served over HTTP renders the lifecycle (SCION-CONNECTOR-CONTROL-R917.4, R917.7)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from scion_sdk.runner.health_server import HealthServer
from scion_sdk.runner.health_state import ComponentStateError, RunnerHealth
from scion_sdk.testing.sinks import InMemorySink

pytestmark = [pytest.mark.spec("SCION-CONNECTOR-CONTROL-R917.7")]


_NAME = "fixture-agent"


@contextmanager
def _served(health: RunnerHealth) -> Iterator[str]:
    server = HealthServer(health, host="127.0.0.1", port=0)
    server.start()
    try:
        host, port = server.address
        yield f"http://{host}:{port}/health/ready"
    finally:
        server.stop()


def _get(url: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _state(url: str) -> tuple[int, str]:
    status, body = _get(url)
    return status, body["connectors"][_NAME]["lifecycle_state"]


def _health() -> RunnerHealth:
    return RunnerHealth(
        sink=InMemorySink(), liveness_window_seconds=30.0, degraded_failures=2
    )


def test_warming_connector_is_not_ready() -> None:
    health = _health()
    health.sync_registry((_NAME,))
    health.record_credentials(_NAME, ok=True)
    with _served(health) as url:
        status, state = _state(url)
        assert (status, state) == (503, "Warming")
        _, body = _get(url)
        assert any(_NAME in reason for reason in body["reasons"])
        health.record_cycle_success(_NAME)
        assert _state(url) == (200, "Ready")


def test_failing_every_cycle_walks_degraded_backoff_ready_stopped() -> None:
    health = _health()
    health.sync_registry((_NAME,))
    health.record_credentials(_NAME, ok=True)
    with _served(health) as url:
        assert _state(url) == (503, "Warming")
        health.record_cycle_failure(_NAME, next_retry_seconds=1.0)
        assert _state(url) == (503, "Degraded")
        health.record_cycle_failure(_NAME, next_retry_seconds=2.0)
        assert _state(url) == (503, "Backoff")
        health.record_cycle_failure(_NAME, next_retry_seconds=4.0)
        assert _state(url) == (503, "Backoff")
        health.record_cycle_success(_NAME)
        assert _state(url) == (200, "Ready")
        health.sync_registry(())
        assert _state(url) == (200, "Stopped")


@pytest.mark.parametrize("failures", [1, 2, 5])
def test_a_connector_with_failures_is_never_ready(failures: int) -> None:
    health = _health()
    health.sync_registry((_NAME,))
    health.record_credentials(_NAME, ok=True)
    for _ in range(failures):
        health.record_cycle_failure(_NAME, next_retry_seconds=None)
    with _served(health) as url:
        assert _get(url)[0] == 503


def _components(url: str) -> dict[str, dict[str, Any]]:
    _, body = _get(url)
    return {item["component_id"]: item for item in body["components"]}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065.3")
def test_ready_body_lists_component_states() -> None:
    health = _health()
    health.sync_registry(("a", "b", "c"), frozenset({"c"}))
    health.record_credentials("a", ok=True)
    health.record_cycle_success("a")
    with _served(health) as url:
        items = _components(url)
        assert items["a"]["state"] == "healthy"
        assert items["a"]["configuration_status"] == "initialized"
        assert items["b"]["state"] == "unavailable"  # Warming after registry load
        assert items["c"]["configuration_status"] == "disabled"
        assert items["c"]["alert_suppression_reason"] == "disabled_by_operator"
        # a bare Unconfigured and a Stopped connector map to not_configured
        health.sync_registry(("a", "c"), frozenset({"c"}))
        assert _components(url)["b"]["state"] == "not_configured"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065.4")
def test_invalid_state_blocks_report() -> None:
    health = _health()
    health.sync_registry((" padded",))
    with _served(health) as url:
        status, body = _get(url)
        assert status == 503
        assert body == {"status": "component_state_invalid", "component": " padded"}
    missing = _health()
    missing.sync_registry(("",))
    with pytest.raises(ComponentStateError) as refused:
        missing.component_states()
    assert refused.value.code == "component_id_missing"
    with _served(missing) as url:
        status, body = _get(url)
        assert (status, body["status"]) == (503, "component_id_missing")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065.5")
def test_body_has_suppression_reason_and_no_secret() -> None:
    health = _health()
    health.sync_registry(("a", "off"), frozenset({"off"}))
    health.record_credentials("a", ok=False, error="credential_unresolved")
    with _served(health) as url:
        _, body = _get(url)
        item = _components(url)["off"]
        assert set(item) == {
            "component_id",
            "state",
            "configuration_status",
            "enabled",
            "last_check_time",
            "alert_suppression_reason",
            "alert",
        }
        assert item["alert"] is False and "token" not in json.dumps(body["components"])


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R065.6")
def test_unconfigured_and_disabled_suppress_alerts() -> None:
    health = _health()
    health.sync_registry(("live", "unset", "off"), frozenset({"off"}))
    health.record_credentials("live", ok=True)
    health.sync_registry(("live", "off"), frozenset({"off"}))  # "unset" is Stopped
    with _served(health) as url:
        items = _components(url)
        assert items["unset"]["state"] == "not_configured"
        assert items["unset"]["alert"] is False and items["off"]["alert"] is False
        health.record_cycle_success("live")
        assert _components(url)["live"]["alert"] is False
        health.record_cycle_failure("live", next_retry_seconds=1.0)
        degraded = _components(url)
        assert degraded["live"]["state"] == "degraded" and degraded["live"]["alert"]
        assert degraded["off"]["alert"] is False and not degraded["unset"]["alert"]
