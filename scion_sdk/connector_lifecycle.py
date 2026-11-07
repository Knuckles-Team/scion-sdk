"""The connector lifecycle: one explicit state machine (SCION-CONNECTOR-CONTROL-R917).

A connector has one :class:`LifecycleState`. :func:`transition` is the one
function that owns every edge; any pair outside :data:`EDGES` raises
:class:`LifecycleTransitionRefused`. ``Warming`` is the SDK name for a
connector whose credentials work but whose first cycle is not yet proven.
``Stopped`` is terminal. The failure count is data held by the caller, never a
state; the caller passes the count with ``cycle_fail``.

The machine persists nothing. :class:`~scion_sdk.runner.health_state.RunnerHealth`
stores the state in ``ConnectorHealth.lifecycle_state``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from scion_sdk._transition_refused import MachineTransitionRefused

__all__ = [
    "DEFAULT_DEGRADED_FAILURES",
    "DEGRADED_FAILURES_ENV",
    "EDGES",
    "Edge",
    "LifecycleEvent",
    "LifecycleState",
    "LifecycleTransitionRefused",
    "degraded_failures_threshold",
    "transition",
]

#: Environment variable holding the consecutive-failure count at which
#: ``Degraded`` becomes ``Backoff``.
DEGRADED_FAILURES_ENV = "SCION_HEALTH_DEGRADED_FAILURES"
#: Starting value of the threshold.
DEFAULT_DEGRADED_FAILURES = 3
_THRESHOLD_RANGE = (1, 1000)


class LifecycleState(StrEnum):
    """The closed states of a connector lifecycle."""

    UNCONFIGURED = "Unconfigured"
    CREDENTIALS_PENDING = "CredentialsPending"
    WARMING = "Warming"
    READY = "Ready"
    DEGRADED = "Degraded"
    BACKOFF = "Backoff"
    STOPPED = "Stopped"


class LifecycleEvent(StrEnum):
    """The closed events; ``cycle_fail`` carries the failure count ``n``."""

    REGISTRY_LOADED = "registry_loaded"
    CREDS_OK = "creds_ok"
    CREDS_BAD = "creds_bad"
    CYCLE_OK = "cycle_ok"
    CYCLE_FAIL = "cycle_fail"
    REMOVED = "removed"


_S = LifecycleState
_E = LifecycleEvent

#: Guard names; the ontology declares them as ``Guard`` classes.
GUARD_AT_THRESHOLD = "FailuresAtThreshold"
GUARD_FIRST_CYCLE = "FirstProvenCycle"


@dataclass(frozen=True)
class Edge:
    """One legal edge of the table.

    ``guard`` names the guard that must hold; ``negated`` means it must not.
    """

    id: str
    source: LifecycleState
    event: LifecycleEvent
    target: LifecycleState
    guard: str = ""
    negated: bool = False


def _edges() -> tuple[Edge, ...]:
    live = (
        (_S.READY, "ready"),
        (_S.DEGRADED, "degraded"),
        (_S.BACKOFF, "backoff"),
    )
    rows = [
        Edge(
            "unconfigured-registry_loaded",
            _S.UNCONFIGURED,
            _E.REGISTRY_LOADED,
            _S.CREDENTIALS_PENDING,
        ),
        Edge("pending-creds_ok", _S.CREDENTIALS_PENDING, _E.CREDS_OK, _S.WARMING),
        Edge(
            "pending-creds_bad",
            _S.CREDENTIALS_PENDING,
            _E.CREDS_BAD,
            _S.CREDENTIALS_PENDING,
        ),
        Edge("warming-cycle_ok", _S.WARMING, _E.CYCLE_OK, _S.READY, GUARD_FIRST_CYCLE),
        Edge("warming-cycle_fail", _S.WARMING, _E.CYCLE_FAIL, _S.DEGRADED),
        Edge("warming-creds_bad", _S.WARMING, _E.CREDS_BAD, _S.CREDENTIALS_PENDING),
        Edge("ready-cycle_ok", _S.READY, _E.CYCLE_OK, _S.READY),
        Edge("ready-cycle_fail", _S.READY, _E.CYCLE_FAIL, _S.DEGRADED),
        Edge(
            "degraded-cycle_fail_below_threshold",
            _S.DEGRADED,
            _E.CYCLE_FAIL,
            _S.DEGRADED,
            GUARD_AT_THRESHOLD,
            True,
        ),
        Edge(
            "degraded-cycle_fail_at_threshold",
            _S.DEGRADED,
            _E.CYCLE_FAIL,
            _S.BACKOFF,
            GUARD_AT_THRESHOLD,
        ),
        Edge("backoff-cycle_fail", _S.BACKOFF, _E.CYCLE_FAIL, _S.BACKOFF),
    ]
    for state, label in live:
        if state is not _S.READY:
            rows.append(Edge(f"{label}-cycle_ok", state, _E.CYCLE_OK, _S.READY))
        rows.append(Edge(f"{label}-creds_ok", state, _E.CREDS_OK, state))
        rows.append(
            Edge(f"{label}-creds_bad", state, _E.CREDS_BAD, _S.CREDENTIALS_PENDING)
        )
    for state, label in (
        (_S.UNCONFIGURED, "unconfigured"),
        (_S.CREDENTIALS_PENDING, "pending"),
        (_S.WARMING, "warming"),
        (_S.READY, "ready"),
        (_S.DEGRADED, "degraded"),
        (_S.BACKOFF, "backoff"),
    ):
        rows.append(Edge(f"{label}-removed", state, _E.REMOVED, _S.STOPPED))
    return tuple(rows)


#: The edge table of SCION-CONNECTOR-CONTROL-R917.1. Every other pair is refused.
EDGES: tuple[Edge, ...] = _edges()

_BY_PAIR: dict[tuple[LifecycleState, LifecycleEvent], tuple[Edge, ...]] = {}
for _edge in EDGES:
    _BY_PAIR[(_edge.source, _edge.event)] = (
        *_BY_PAIR.get((_edge.source, _edge.event), ()),
        _edge,
    )


class LifecycleTransitionRefused(MachineTransitionRefused):
    """An event the lifecycle does not declare in the current state (R917.2).

    Carries ``machine`` (``ConnectorLifecycle``), ``from_state``, ``event`` and
    ``reason``; its code is ``LIFECYCLE_TRANSITION_REFUSED``. It maps to the
    core ``TransitionRefused`` class.
    """

    code: ClassVar[str] = "LIFECYCLE_TRANSITION_REFUSED"
    machine_name: ClassVar[str] = "ConnectorLifecycle"


def degraded_failures_threshold(environ: dict[str, str] | None = None) -> int:
    """The failure count at which ``Degraded`` becomes ``Backoff``.

    Reads :data:`DEGRADED_FAILURES_ENV`; the starting value is
    :data:`DEFAULT_DEGRADED_FAILURES`.

    Raises:
        ValueError: the value is not an integer inside the allowed range.
    """
    raw = (os.environ if environ is None else environ).get(DEGRADED_FAILURES_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_DEGRADED_FAILURES
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{DEGRADED_FAILURES_ENV} must be an integer") from None
    low, high = _THRESHOLD_RANGE
    if not low <= value <= high:
        raise ValueError(f"{DEGRADED_FAILURES_ENV} must be within {low}..{high}")
    return value


def _holds(edge: Edge, failures: int, threshold: int) -> bool:
    if edge.guard != GUARD_AT_THRESHOLD:
        return True
    return (failures >= threshold) is not edge.negated


def transition(
    state: LifecycleState,
    event: LifecycleEvent,
    *,
    failures: int = 0,
    threshold: int = DEFAULT_DEGRADED_FAILURES,
) -> LifecycleState:
    """The one function that owns every lifecycle edge.

    Args:
        state: The current state.
        event: The event to apply.
        failures: For ``cycle_fail``, the consecutive failure count ``n``
            including this failure.
        threshold: The count at which ``Degraded`` becomes ``Backoff``.

    Raises:
        LifecycleTransitionRefused: the pair is not an edge of :data:`EDGES`.
    """
    for edge in _BY_PAIR.get((state, event), ()):
        if _holds(edge, failures, threshold):
            return edge.target
    reason = "Stopped is terminal" if state is _S.STOPPED else ""
    raise LifecycleTransitionRefused(state.value, event.value, reason)
