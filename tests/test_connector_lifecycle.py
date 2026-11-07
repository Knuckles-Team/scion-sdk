"""The connector lifecycle machine (SCION-CONNECTOR-CONTROL-R917.1, R917.2)."""

from __future__ import annotations

import pytest

from scion_sdk.connector_lifecycle import (
    DEFAULT_DEGRADED_FAILURES,
    DEGRADED_FAILURES_ENV,
    EDGES,
    Edge,
    LifecycleEvent,
    LifecycleState,
    LifecycleTransitionRefused,
    degraded_failures_threshold,
    transition,
)
from scion_sdk.transition_refused import MachineTransitionRefused

pytestmark = [
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R917.1"),
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R917.2"),
]


_THRESHOLD = 3
_LEGAL_PAIRS = {(edge.source, edge.event) for edge in EDGES}
_ILLEGAL = [
    pytest.param(state, event, id=f"{state.value}-{event.value}")
    for state in LifecycleState
    for event in LifecycleEvent
    if (state, event) not in _LEGAL_PAIRS
]
#: The failure count each edge is exercised with.
_FAILURES = {
    "degraded-cycle_fail_below_threshold": _THRESHOLD - 1,
    "degraded-cycle_fail_at_threshold": _THRESHOLD,
}


def test_edge_ids_are_the_declared_parameter_ids() -> None:
    assert all(isinstance(edge, Edge) for edge in EDGES)
    assert len(EDGES) == 25
    assert len({edge.id for edge in EDGES}) == 25


@pytest.mark.parametrize("edge", [pytest.param(e, id=e.id) for e in EDGES])
def test_each_legal_edge_moves_state(edge) -> None:
    got = transition(
        edge.source,
        edge.event,
        failures=_FAILURES.get(edge.id, 1),
        threshold=_THRESHOLD,
    )
    assert got is edge.target


@pytest.mark.parametrize(("state", "event"), _ILLEGAL)
def test_every_illegal_pair_is_refused_with_the_typed_error(state, event) -> None:
    with pytest.raises(LifecycleTransitionRefused) as raised:
        transition(state, event, failures=1, threshold=_THRESHOLD)
    assert raised.value.from_state == state.value
    assert raised.value.event == event.value


def test_refusal_carries_its_fields_and_the_state_is_unchanged() -> None:
    state = LifecycleState.CREDENTIALS_PENDING
    with pytest.raises(LifecycleTransitionRefused) as raised:
        transition(state, LifecycleEvent.CYCLE_OK)
    refusal = raised.value
    assert isinstance(refusal, MachineTransitionRefused)
    assert refusal.machine == "ConnectorLifecycle"
    assert refusal.code == "LIFECYCLE_TRANSITION_REFUSED"
    assert (refusal.from_state, refusal.event) == ("CredentialsPending", "cycle_ok")
    assert refusal.reason
    assert state is LifecycleState.CREDENTIALS_PENDING


def test_the_declared_refusals_hold() -> None:
    for event in LifecycleEvent:
        assert (LifecycleState.STOPPED, event) not in _LEGAL_PAIRS
    refused = {
        LifecycleState.UNCONFIGURED: {
            "creds_ok",
            "creds_bad",
            "cycle_ok",
            "cycle_fail",
        },
        LifecycleState.CREDENTIALS_PENDING: {
            "cycle_ok",
            "cycle_fail",
            "registry_loaded",
        },
        LifecycleState.WARMING: {"registry_loaded", "creds_ok"},
    }
    for state, events in refused.items():
        for name in events:
            assert (state, LifecycleEvent(name)) not in _LEGAL_PAIRS


def test_the_first_proven_cycle_walk_never_skips_warming() -> None:
    state = LifecycleState.UNCONFIGURED
    for event in (
        LifecycleEvent.REGISTRY_LOADED,
        LifecycleEvent.CREDS_OK,
    ):
        state = transition(state, event)
    assert state is LifecycleState.WARMING
    assert transition(state, LifecycleEvent.CYCLE_OK) is LifecycleState.READY


def test_threshold_reads_the_environment_with_a_bounded_range() -> None:
    assert degraded_failures_threshold({}) == DEFAULT_DEGRADED_FAILURES
    assert degraded_failures_threshold({DEGRADED_FAILURES_ENV: "7"}) == 7
    for bad in ("zero", "0", "1001"):
        with pytest.raises(ValueError, match=DEGRADED_FAILURES_ENV):
            degraded_failures_threshold({DEGRADED_FAILURES_ENV: bad})
