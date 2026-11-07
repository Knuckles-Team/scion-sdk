"""Runner health state: a supervisor heartbeat, per-connector detail, readiness.

Nothing here opens a socket -- :mod:`scion_sdk.runner.health_server`
serves this state over HTTP. Every synchronous method (everything but
:meth:`RunnerHealth.readiness`) is safe to call from a different thread than
the writers: the HTTP listener runs in its own background thread while the
supervisor's anyio event loop keeps updating this object. ``readiness`` is
``async`` -- it asks the configured sink directly -- so its caller supplies
the event loop (``health_server.py`` runs one fresh per call, since the HTTP
listener's thread has none of its own).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from scion_sdk.connector_lifecycle import (
    LifecycleEvent,
    LifecycleState,
    LifecycleTransitionRefused,
    degraded_failures_threshold,
    transition,
)
from scion_sdk.ports.sink import Sink
from scion_sdk.runner.sink_probe import probe_sink_readiness, sink_reason

__all__ = ["ConnectorHealth", "HealthReport", "RunnerHealth"]

_logger = logging.getLogger(__name__)
_SCHEDULER_COMPONENT = "scheduler_loop"
#: Default bound on a per-request sink readiness probe (see ``sink_probe.py``).
_DEFAULT_SINK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ConnectorHealth:
    """One connector's last-known cycle and credential outcome.

    ``lifecycle_state`` is the one persisted state (SCION-CONNECTOR-CONTROL-R917);
    readiness is derived from it. ``consecutive_failures`` is a data counter and
    nothing derives readiness from it.
    """

    connector: str
    lifecycle_state: LifecycleState = LifecycleState.UNCONFIGURED
    last_success_monotonic: float | None = None
    consecutive_failures: int = 0
    next_retry_seconds: float | None = None
    credential_error: str = ""

    @property
    def ready(self) -> bool:
        """Whether the connector is ``Ready``; derived, never stored."""
        return self.lifecycle_state is LifecycleState.READY

    def snapshot(self, *, now: float) -> dict[str, object]:
        """A JSON-safe view. Never carries a credential value."""
        since = (
            None
            if self.last_success_monotonic is None
            else now - self.last_success_monotonic
        )
        return {
            "lifecycle_state": self.lifecycle_state.value,
            "last_success_seconds_ago": since,
            "consecutive_failures": self.consecutive_failures,
            "next_retry_seconds": self.next_retry_seconds,
            "credential_error": self.credential_error,
        }


@dataclass(frozen=True)
class HealthReport:
    """One health response: the HTTP status and its JSON body."""

    ok: bool
    status: int
    body: dict[str, object]


class RunnerHealth:
    """Liveness/readiness state the supervisor loop and workers update.

    Args:
        sink: The configured sink; readiness calls ``sink.readiness()``.
        liveness_window_seconds: How long the scheduler heartbeat may go
            without updating before liveness reports unhealthy.
        sink_timeout_seconds: Bound on the per-request sink readiness probe,
            so a hung sink cannot hang a ``/health/ready`` request.
        clock: Monotonic time source; overridden by tests for determinism.
        degraded_failures: Consecutive failures at which ``Degraded`` becomes
            ``Backoff``; defaults to ``SCION_HEALTH_DEGRADED_FAILURES``.
    """

    def __init__(
        self,
        *,
        sink: Sink,
        liveness_window_seconds: float,
        sink_timeout_seconds: float = _DEFAULT_SINK_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        degraded_failures: int | None = None,
    ) -> None:
        self._sink = sink
        self._sink_timeout = sink_timeout_seconds
        self._window = liveness_window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._heartbeat = clock()
        self._registry_loaded = False
        self._connectors: dict[str, ConnectorHealth] = {}
        self._threshold = (
            degraded_failures_threshold()
            if degraded_failures is None
            else degraded_failures
        )

    def _apply(
        self,
        current: ConnectorHealth,
        event: LifecycleEvent,
        *,
        failures: int | None = None,
        **changes: Any,
    ) -> ConnectorHealth:
        """Move ``current`` through the one transition function.

        ``failures`` is the count a ``cycle_fail`` carries and is stored as
        ``consecutive_failures`` when the edge is legal. A refused event is
        logged and the record stays unchanged.
        """
        count = current.consecutive_failures if failures is None else failures
        try:
            state = transition(
                current.lifecycle_state,
                event,
                failures=count,
                threshold=self._threshold,
            )
        except LifecycleTransitionRefused as refusal:
            _logger.info(
                "connector %s lifecycle refused %s in %s",
                current.connector,
                refusal.event,
                refusal.from_state,
            )
            return current
        if failures is not None:
            changes["consecutive_failures"] = failures
        return replace(current, lifecycle_state=state, **changes)

    def heartbeat(self) -> None:
        """Record that the scheduler loop completed another iteration."""
        with self._lock:
            self._heartbeat = self._clock()

    def sync_registry(self, connectors: tuple[str, ...]) -> None:
        """Mark the registry loaded; start new connectors, stop removed ones.

        A connector unseen or ``Stopped`` gets a fresh machine and
        ``registry_loaded``. A connector no longer listed gets ``removed``.
        """
        wanted = set(connectors)
        with self._lock:
            self._registry_loaded = True
            for name in connectors:
                known = self._connectors.get(name)
                if known is None or known.lifecycle_state is LifecycleState.STOPPED:
                    fresh = ConnectorHealth(connector=name)
                    self._connectors[name] = self._apply(
                        fresh, LifecycleEvent.REGISTRY_LOADED
                    )
            for name, health in self._connectors.items():
                if name not in wanted and health.lifecycle_state is not (
                    LifecycleState.STOPPED
                ):
                    self._connectors[name] = self._apply(
                        health, LifecycleEvent.REMOVED, next_retry_seconds=None
                    )

    def record_credentials(self, connector: str, *, ok: bool, error: str = "") -> None:
        """Send ``creds_ok`` or ``creds_bad`` for ``connector``.

        ``creds_ok`` clears the credential error and keeps the failure count,
        except from ``CredentialsPending``, where it resets the count.
        """
        with self._lock:
            current = self._connectors.get(connector, ConnectorHealth(connector))
            if not ok:
                updated = self._apply(
                    current, LifecycleEvent.CREDS_BAD, credential_error=error
                )
            elif current.lifecycle_state is LifecycleState.CREDENTIALS_PENDING:
                updated = self._apply(
                    current,
                    LifecycleEvent.CREDS_OK,
                    credential_error="",
                    failures=0,
                )
            else:
                updated = self._apply(
                    current, LifecycleEvent.CREDS_OK, credential_error=""
                )
            self._connectors[connector] = updated

    def record_cycle_success(self, connector: str) -> None:
        """Send ``cycle_ok``: reset the failure count and prove the cycle."""
        with self._lock:
            current = self._connectors.get(connector, ConnectorHealth(connector))
            self._connectors[connector] = self._apply(
                current,
                LifecycleEvent.CYCLE_OK,
                failures=0,
                last_success_monotonic=self._clock(),
                next_retry_seconds=None,
            )

    def record_cycle_failure(
        self, connector: str, *, next_retry_seconds: float | None
    ) -> None:
        """Send ``cycle_fail(n)`` with the new consecutive failure count."""
        with self._lock:
            current = self._connectors.get(connector, ConnectorHealth(connector))
            self._connectors[connector] = self._apply(
                current,
                LifecycleEvent.CYCLE_FAIL,
                failures=current.consecutive_failures + 1,
                next_retry_seconds=next_retry_seconds,
            )

    def liveness(self) -> HealthReport:
        """200 while the scheduler heartbeat is fresh; 503 naming it stale."""
        with self._lock:
            age = self._clock() - self._heartbeat
        if age <= self._window:
            return HealthReport(
                True, 200, {"status": "ok", "heartbeat_age_seconds": age}
            )
        body = {
            "status": "error",
            "component": _SCHEDULER_COMPONENT,
            "age_seconds": age,
            "max_age_seconds": self._window,
        }
        return HealthReport(False, 503, body)

    async def readiness(self) -> HealthReport:
        """200 only once the registry loaded, every connector is ``Ready``, sink usable.

        A ``Stopped`` (removed) connector does not hold readiness. 503 with
        every reason otherwise, plus per-connector detail (no secrets).
        The sink is asked directly (``sink.readiness()``), bounded by
        ``sink_timeout_seconds`` so a hung sink cannot hang this call.
        """
        with self._lock:
            registry_loaded = self._registry_loaded
            connectors = dict(self._connectors)
        reasons = [] if registry_loaded else ["registry not loaded"]
        unready = sorted(
            f"{name} ({health.lifecycle_state.value})"
            for name, health in connectors.items()
            if not health.ready and health.lifecycle_state is not LifecycleState.STOPPED
        )
        if unready:
            reasons.append(f"connectors not ready: {', '.join(unready)}")
        sink_state = await probe_sink_readiness(
            self._sink, timeout_seconds=self._sink_timeout
        )
        if not sink_state.ready:
            reasons.append(sink_reason(sink_state))
        now = self._clock()
        body: dict[str, object] = {
            "status": "ready" if not reasons else "not_ready",
            "reasons": reasons,
            "connectors": {
                name: health.snapshot(now=now) for name, health in connectors.items()
            },
        }
        return HealthReport(not reasons, 200 if not reasons else 503, body)
