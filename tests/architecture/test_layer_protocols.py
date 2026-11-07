"""Lower layers name their collaborators through structural protocols.

The circuit breaker and websocket stream report cycle outcomes to a
:class:`CycleHealth`; the HTTP retry policy consults a :class:`CallBreaker`.
These tests prove the real runner health and circuit breaker satisfy them, so
the protocols stand in for the upward imports the layer order forbids
(SCION-CENTRALIZED-CAPABILITIES-R044).
"""

from __future__ import annotations

import pytest

from scion_sdk.connector_lifecycle import CycleHealth
from scion_sdk.http.retry import CallBreaker, RetryPolicy
from scion_sdk.resilience import CircuitBreaker
from scion_sdk.runner.health_state import RunnerHealth
from scion_sdk.testing.sinks import InMemorySink

pytestmark = pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")


def test_runner_health_is_a_cycle_health() -> None:
    health: CycleHealth = RunnerHealth(
        sink=InMemorySink(), liveness_window_seconds=30.0
    )
    health.record_cycle_failure("demo", next_retry_seconds=None)
    health.record_cycle_success("demo")


def test_circuit_breaker_is_a_call_breaker() -> None:
    breaker: CallBreaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)
    breaker.before_call()
    breaker.settle(ok=True)
    assert RetryPolicy(breaker=breaker).breaker is breaker
