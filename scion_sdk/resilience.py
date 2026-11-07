"""Explicitly retry-safe callable execution and the circuit breaker.

:class:`CircuitBreaker` is one explicit state machine
(SCION-CONNECTOR-CONTROL-R918): ``Closed``, ``Open`` and ``HalfOpen``, one
``transition`` that owns every edge, and ``state`` as the one stored field.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, cast

import anyio

from scion_sdk.http.retry import RetryPolicy, is_tls_verification_failure
from scion_sdk.transition_refused import MachineTransitionRefused

if TYPE_CHECKING:
    from scion_sdk.connector_lifecycle import CycleHealth

__all__ = [
    "BREAKER_COOLDOWN_ENV",
    "BREAKER_FAILURES_ENV",
    "BreakerState",
    "BreakerTransitionRefused",
    "CircuitBreaker",
    "CircuitOpen",
    "run_with_resilience",
    "run_with_resilience_sync",
]

#: Consecutive failures at which a ``Closed`` breaker opens (starting value 5, range 1..100).
BREAKER_FAILURES_ENV = "SCION_BREAKER_FAILURES"
#: Seconds an ``Open`` breaker waits before a probe (starting value 30, range 1..3600).
BREAKER_COOLDOWN_ENV = "SCION_BREAKER_COOLDOWN_S"
_DEFAULT_FAILURES = 5
_DEFAULT_COOLDOWN_S = 30.0


class BreakerState(StrEnum):
    """The closed states of a circuit breaker; none is terminal."""

    CLOSED = "Closed"
    OPEN = "Open"
    HALF_OPEN = "HalfOpen"


class BreakerEvent(StrEnum):
    """The closed events of a circuit breaker."""

    CALL_OK = "call_ok"
    CALL_FAIL = "call_fail"
    COOLDOWN_ELAPSED = "cooldown_elapsed"


class CircuitOpen(Exception):
    """The breaker is ``Open``: the call is refused before any network call."""

    code: ClassVar[str] = "CIRCUIT_OPEN"

    def __init__(self, retry_in_seconds: float = 0.0) -> None:
        self.retry_in_seconds = max(0.0, retry_in_seconds)
        super().__init__(f"{self.code}: retry in {self.retry_in_seconds:.1f}s")


class BreakerTransitionRefused(MachineTransitionRefused):
    """An event the breaker does not declare in its state (R918.2)."""

    code: ClassVar[str] = "BREAKER_TRANSITION_REFUSED"
    machine_name: ClassVar[str] = "CircuitBreaker"


_B = BreakerState
_BE = BreakerEvent
#: The legal pairs of R918.1; ``call_fail`` in ``Closed`` is guarded by the threshold.
_LEGAL: frozenset[tuple[BreakerState, BreakerEvent]] = frozenset(
    {
        (_B.CLOSED, _BE.CALL_OK),
        (_B.CLOSED, _BE.CALL_FAIL),
        (_B.OPEN, _BE.COOLDOWN_ELAPSED),
        (_B.HALF_OPEN, _BE.CALL_OK),
        (_B.HALF_OPEN, _BE.CALL_FAIL),
    }
)


def _bounded_env(
    environ: Mapping[str, str], name: str, default: float, low: float, high: float
) -> float:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be within {low:g}..{high:g}")
    return value


class CircuitBreaker:
    """Opens after repeated failures and probes once after a cooldown.

    ``state`` is the one stored field and lives in memory. When ``health`` and
    ``connector`` are given, entering ``Open`` sends ``cycle_fail`` and
    returning to ``Closed`` sends ``cycle_ok`` to the connector lifecycle of
    SCION-CONNECTOR-CONTROL-R917; the lifecycle keeps no copy of the state.

    Args:
        failure_threshold: Consecutive failures that open it; defaults to
            ``SCION_BREAKER_FAILURES`` (5, range 1..100).
        cooldown_seconds: Wait before the probe; defaults to
            ``SCION_BREAKER_COOLDOWN_S`` (30, range 1..3600).
        clock: Monotonic time source.
        health: The runner health that mirrors the breaker into the lifecycle.
        connector: The connector name reported to ``health``.
    """

    def __init__(
        self,
        *,
        failure_threshold: int | None = None,
        cooldown_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        health: CycleHealth | None = None,
        connector: str = "",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if environ is None else environ
        if failure_threshold is None:
            failure_threshold = int(
                _bounded_env(env, BREAKER_FAILURES_ENV, _DEFAULT_FAILURES, 1, 100)
            )
        if cooldown_seconds is None:
            cooldown_seconds = _bounded_env(
                env, BREAKER_COOLDOWN_ENV, _DEFAULT_COOLDOWN_S, 1, 3600
            )
        if not 1 <= failure_threshold <= 100 or not 1 <= cooldown_seconds <= 3600:
            raise ValueError("breaker needs 1..100 failures and 1..3600 s cooldown")
        self.state: BreakerState = BreakerState.CLOSED
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._health = health
        self._connector = connector
        self._failures = 0
        self._opened_at = 0.0
        self._probe_out = False
        self._lock = threading.RLock()

    def transition(self, event: BreakerEvent) -> BreakerState:
        """The one function that owns every breaker edge.

        Raises:
            BreakerTransitionRefused: the pair is outside the table.
        """
        with self._lock:
            if (self.state, event) not in _LEGAL:
                raise BreakerTransitionRefused(self.state.value, event.value)
            previous = self.state
            self.state = self._next(event)
            self._on_enter(previous)
            return self.state

    def _next(self, event: BreakerEvent) -> BreakerState:
        if event is _BE.COOLDOWN_ELAPSED:
            return _B.HALF_OPEN
        if event is _BE.CALL_OK:
            self._failures = 0
            return _B.CLOSED
        self._failures += 1
        if self.state is _B.HALF_OPEN or self._failures >= self._threshold:
            return _B.OPEN
        return _B.CLOSED

    def _on_enter(self, previous: BreakerState) -> None:
        self._probe_out = self.state is _B.HALF_OPEN
        if self.state is _B.OPEN:
            self._opened_at = self._clock()
            self._report(failed=True)
        elif self.state is _B.CLOSED and previous is not _B.CLOSED:
            self._report(failed=False)

    def _report(self, *, failed: bool) -> None:
        if self._health is None:
            return
        if failed:
            self._health.record_cycle_failure(self._connector, next_retry_seconds=None)
        else:
            self._health.record_cycle_success(self._connector)

    def before_call(self) -> None:
        """Admit a call or refuse it before any network call.

        ``Open`` refuses with :class:`CircuitOpen` until the cooldown ends;
        then ``HalfOpen`` admits exactly one probe.

        Raises:
            CircuitOpen: the breaker is open, or its one probe is in flight.
        """
        with self._lock:
            if self.state is _B.OPEN:
                waited = self._clock() - self._opened_at
                if waited < self._cooldown:
                    raise CircuitOpen(self._cooldown - waited)
                self.transition(_BE.COOLDOWN_ELAPSED)
            elif self.state is _B.HALF_OPEN and self._probe_out:
                raise CircuitOpen(0.0)
            self._probe_out = self.state is _B.HALF_OPEN

    def settle(self, *, ok: bool) -> None:
        """Report the outcome of an admitted call.

        A late outcome that arrives while ``Open`` is ignored: the breaker
        already opened and a probe decides the next edge.
        """
        with self._lock:
            if self.state is _B.OPEN:
                return
            self.transition(_BE.CALL_OK if ok else _BE.CALL_FAIL)


def _validate(
    policy: RetryPolicy,
    retry_on: tuple[type[Exception], ...],
    *,
    retry_safe: bool,
    fallback_count: int,
) -> None:
    if (policy.max_attempts > 1 or fallback_count) and retry_safe is not True:
        raise ValueError("retries and fallbacks require retry-safe operations")
    if fallback_count > 10:
        raise ValueError("at most ten fallbacks are allowed")
    if not isinstance(retry_on, tuple) or not all(
        isinstance(item, type) and issubclass(item, Exception) and item is not Exception
        for item in retry_on
    ):
        raise ValueError("retry_on must be a tuple of exception types")


def _plan[T](
    primary: Callable[[], T],
    fallbacks: tuple[Callable[[], T], ...],
    policy: RetryPolicy,
) -> list[Callable[[], T]]:
    return [primary] * policy.max_attempts + list(fallbacks)


def _retryable(error: Exception, retry_on: tuple[type[Exception], ...]) -> bool:
    if isinstance(error, (TimeoutError, PermissionError, TypeError, ValueError)):
        return False
    return isinstance(error, retry_on) and not is_tls_verification_failure(error)


def _validate_timeout(timeout_seconds: float | None) -> None:
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout must be finite and positive")


async def _timed[T](
    call: Callable[[], Awaitable[T]], timeout_seconds: float | None
) -> T:
    if timeout_seconds is None:
        return await call()
    with anyio.fail_after(timeout_seconds):
        return await call()


async def _attempt[T](
    call: Callable[[], Awaitable[T]],
    timeout_seconds: float | None,
    *,
    last: bool,
    retry_on: tuple[type[Exception], ...],
) -> tuple[bool, T | None]:
    try:
        return True, await _timed(call, timeout_seconds)
    except Exception as exc:
        if last or not _retryable(exc, retry_on):
            raise
    return False, None


def _attempt_sync[T](
    call: Callable[[], T], *, last: bool, retry_on: tuple[type[Exception], ...]
) -> tuple[bool, T | None]:
    try:
        return True, call()
    except Exception as exc:
        if last or not _retryable(exc, retry_on):
            raise
    return False, None


async def run_with_resilience[T](
    primary: Callable[[], Awaitable[T]],
    *,
    retry_on: tuple[type[Exception], ...],
    retry_safe: bool,
    policy: RetryPolicy | None = None,
    timeout_seconds: float | None = None,
    fallbacks: tuple[Callable[[], Awaitable[T]], ...] = (),
) -> T:
    """Run an async callable under explicit retry and fallback policy.

    Repeated calls and fallbacks require the caller to affirm they are safe.
    Timeout and cancellation propagate without another call: an interrupted
    operation may already have produced an effect. The timeout is cooperative;
    a coroutine that suppresses cancellation cannot be forcibly stopped.
    """
    selected = policy or RetryPolicy(max_attempts=1)
    _validate(selected, retry_on, retry_safe=retry_safe, fallback_count=len(fallbacks))
    _validate_timeout(timeout_seconds)
    calls = _plan(primary, fallbacks, selected)
    for index, call in enumerate(calls):
        if 0 < index < selected.max_attempts:
            await anyio.sleep(selected.backoff(index))
        done, value = await _attempt(
            call, timeout_seconds, last=index == len(calls) - 1, retry_on=retry_on
        )
        if done:
            return cast("T", value)
    raise RuntimeError("resilience policy exhausted without a result")


def run_with_resilience_sync[T](
    primary: Callable[[], T],
    *,
    retry_on: tuple[type[Exception], ...],
    retry_safe: bool,
    policy: RetryPolicy | None = None,
    timeout_seconds: float | None = None,
    fallbacks: tuple[Callable[[], T], ...] = (),
) -> T:
    """Run a blocking callable without claiming to enforce a timeout.

    Requested timeouts are refused before any call because an arbitrary
    blocking function cannot be stopped safely. Do not call this from an event
    loop; use the async runner for cancellable async operations.
    """
    selected = policy or RetryPolicy(max_attempts=1)
    _validate(selected, retry_on, retry_safe=retry_safe, fallback_count=len(fallbacks))
    if timeout_seconds is not None:
        raise ValueError("timeouts are unavailable for blocking callables")
    calls = _plan(primary, fallbacks, selected)
    for index, call in enumerate(calls):
        if 0 < index < selected.max_attempts:
            time.sleep(selected.backoff(index))
        done, value = _attempt_sync(
            call, last=index == len(calls) - 1, retry_on=retry_on
        )
        if done:
            return cast("T", value)
    raise RuntimeError("resilience policy exhausted without a result")
