"""The circuit breaker machine (SCION-CONNECTOR-CONTROL-R918)."""

from __future__ import annotations

import logging

import httpx
import pytest

from scion_sdk.http.retry import RetryPolicy
from scion_sdk.http.transport import GovernedTransport
from scion_sdk.resilience import (
    BREAKER_COOLDOWN_ENV,
    BREAKER_FAILURES_ENV,
    BreakerEvent,
    BreakerState,
    BreakerTransitionRefused,
    CircuitBreaker,
    CircuitOpen,
)
from scion_sdk.runner.health_state import RunnerHealth
from scion_sdk.testing.sinks import InMemorySink

pytestmark = [
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R918"),
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R918.1"),
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R918.2"),
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R918.3"),
]


_THRESHOLD = 3
_COOLDOWN = 10.0


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _breaker(clock: _Clock, **extra: object) -> CircuitBreaker:
    return CircuitBreaker(
        failure_threshold=_THRESHOLD,
        cooldown_seconds=_COOLDOWN,
        clock=clock,
        **extra,  # type: ignore[arg-type]
    )


def _open(breaker: CircuitBreaker) -> None:
    for _ in range(_THRESHOLD):
        breaker.transition(BreakerEvent.CALL_FAIL)


def _in_state(breaker: CircuitBreaker, clock: _Clock, state: BreakerState) -> None:
    if state is not BreakerState.CLOSED:
        _open(breaker)
    if state is BreakerState.HALF_OPEN:
        breaker.transition(BreakerEvent.COOLDOWN_ELAPSED)


_LEGAL = [
    ("closed-call_ok", BreakerState.CLOSED, BreakerEvent.CALL_OK, BreakerState.CLOSED),
    (
        "closed-call_fail_below_threshold",
        BreakerState.CLOSED,
        BreakerEvent.CALL_FAIL,
        BreakerState.CLOSED,
    ),
    (
        "closed-call_fail_at_threshold",
        BreakerState.CLOSED,
        BreakerEvent.CALL_FAIL,
        BreakerState.OPEN,
    ),
    (
        "open-cooldown_elapsed",
        BreakerState.OPEN,
        BreakerEvent.COOLDOWN_ELAPSED,
        BreakerState.HALF_OPEN,
    ),
    (
        "halfopen-call_ok",
        BreakerState.HALF_OPEN,
        BreakerEvent.CALL_OK,
        BreakerState.CLOSED,
    ),
    (
        "halfopen-call_fail",
        BreakerState.HALF_OPEN,
        BreakerEvent.CALL_FAIL,
        BreakerState.OPEN,
    ),
]


@pytest.mark.parametrize(
    ("source", "event", "target"),
    [pytest.param(s, e, t, id=name) for name, s, e, t in _LEGAL],
)
def test_each_legal_edge_moves_state(source, event, target) -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    _in_state(breaker, clock, source)
    if target is BreakerState.OPEN and source is BreakerState.CLOSED:
        for _ in range(_THRESHOLD - 1):
            breaker.transition(BreakerEvent.CALL_FAIL)
    assert breaker.transition(event) is target
    assert breaker.state is target


_ILLEGAL = [
    ("closed-cooldown_elapsed", BreakerState.CLOSED, BreakerEvent.COOLDOWN_ELAPSED),
    ("open-call_ok", BreakerState.OPEN, BreakerEvent.CALL_OK),
    ("open-call_fail", BreakerState.OPEN, BreakerEvent.CALL_FAIL),
    (
        "halfopen-cooldown_elapsed",
        BreakerState.HALF_OPEN,
        BreakerEvent.COOLDOWN_ELAPSED,
    ),
]


@pytest.mark.parametrize(
    ("state", "event"), [pytest.param(s, e, id=name) for name, s, e in _ILLEGAL]
)
def test_every_illegal_pair_is_refused_with_the_typed_error(state, event) -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    _in_state(breaker, clock, state)
    with pytest.raises(BreakerTransitionRefused) as raised:
        breaker.transition(event)
    refusal = raised.value
    assert refusal.code == "BREAKER_TRANSITION_REFUSED"
    assert (refusal.machine, refusal.from_state, refusal.event) == (
        "CircuitBreaker",
        state.value,
        event.value,
    )
    assert breaker.state is state


def test_breaker_walks_closed_open_half_open_closed() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    assert breaker.state is BreakerState.CLOSED
    for _ in range(_THRESHOLD):
        breaker.before_call()
        breaker.settle(ok=False)
    assert breaker.state is BreakerState.OPEN
    clock.now += _COOLDOWN
    breaker.before_call()
    assert breaker.state is BreakerState.HALF_OPEN
    breaker.settle(ok=True)
    assert breaker.state is BreakerState.CLOSED


def test_a_success_resets_the_consecutive_failure_count() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    for _ in range(_THRESHOLD - 1):
        breaker.settle(ok=False)
    breaker.settle(ok=True)
    for _ in range(_THRESHOLD - 1):
        breaker.settle(ok=False)
    assert breaker.state is BreakerState.CLOSED


def test_open_refuses_without_network_and_half_open_admits_one_probe() -> None:
    clock = _Clock()
    breaker = _breaker(clock)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(503)

    policy = RetryPolicy(max_attempts=1, jitter=False, breaker=breaker)
    transport = GovernedTransport(
        httpx.MockTransport(handler),
        policy=policy,
        logger=logging.getLogger("breaker-test"),
        sleep=lambda _seconds: None,
    )
    client = httpx.Client(transport=transport, base_url="https://upstream.invalid")
    for _ in range(_THRESHOLD):
        assert client.get("/x").status_code == 503
    assert breaker.state is BreakerState.OPEN
    assert len(sent) == _THRESHOLD

    with pytest.raises(CircuitOpen) as refused:
        client.get("/x")
    assert refused.value.code == "CIRCUIT_OPEN"
    assert len(sent) == _THRESHOLD

    clock.now += _COOLDOWN
    assert client.get("/x").status_code == 503
    assert len(sent) == _THRESHOLD + 1
    assert breaker.state is BreakerState.OPEN

    clock.now += _COOLDOWN
    breaker.before_call()
    assert breaker.state is BreakerState.HALF_OPEN
    with pytest.raises(CircuitOpen):
        breaker.before_call()


def test_open_breaker_makes_lifecycle_degraded() -> None:
    clock = _Clock()
    health = RunnerHealth(sink=InMemorySink(), liveness_window_seconds=30.0)
    health.sync_registry(("fixture",))
    health.record_credentials("fixture", ok=True)
    health.record_cycle_success("fixture")
    breaker = _breaker(clock, health=health, connector="fixture")

    def lifecycle() -> str:
        return health._connectors["fixture"].lifecycle_state.value

    assert lifecycle() == "Ready"
    _open(breaker)
    assert lifecycle() == "Degraded"
    clock.now += _COOLDOWN
    breaker.before_call()
    breaker.settle(ok=True)
    assert breaker.state is BreakerState.CLOSED
    assert lifecycle() == "Ready"


def test_configuration_comes_from_bounded_environment_values() -> None:
    breaker = CircuitBreaker(
        environ={BREAKER_FAILURES_ENV: "2", BREAKER_COOLDOWN_ENV: "5"}
    )
    breaker.settle(ok=False)
    breaker.settle(ok=False)
    assert breaker.state is BreakerState.OPEN
    for bad in ({BREAKER_FAILURES_ENV: "0"}, {BREAKER_COOLDOWN_ENV: "9999"}):
        with pytest.raises(ValueError, match="SCION_BREAKER"):
            CircuitBreaker(environ=bad)
    assert CircuitBreaker(environ={}).state is BreakerState.CLOSED
