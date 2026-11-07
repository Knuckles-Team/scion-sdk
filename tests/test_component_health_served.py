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
from scion_sdk.runner.health_state import RunnerHealth
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
