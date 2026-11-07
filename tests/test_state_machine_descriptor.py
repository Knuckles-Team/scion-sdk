"""The state machine descriptor and its one pure transition function (SCION-GOVERNED-WRITEBACK-R007.1, R007.2)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from state_machine_fixtures import SUBMIT_AND_CANCEL

from scion_sdk.state_machine import (
    GuardDecl,
    StateMachineDescriptor,
    StateTransitionRefused,
    TransitionDecl,
    next_state,
)

pytestmark = [
    pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R007.1"),
    pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R007.2"),
]


_BASE = {
    "name": "Order",
    "state_field": "status",
    "states": ("open", "closed"),
    "initial": "open",
    "terminals": ("closed",),
    "transitions": (TransitionDecl("open", "close", "closed"),),
}
_DESCRIPTORS = {
    "duplicate_state": {"states": ("open", "closed", "open")},
    "unknown_target": {"transitions": (TransitionDecl("open", "close", "gone"),)},
    "terminal_with_exit": {
        "transitions": (
            TransitionDecl("open", "close", "closed"),
            TransitionDecl("closed", "reopen", "open"),
        )
    },
    "undeclared_initial": {"initial": "pending"},
    "unknown_guard": {"transitions": (TransitionDecl("open", "close", "closed", "g"),)},
    "valid": {},
}
_FACTS = {"line_count": 2}


@pytest.mark.parametrize("case", [pytest.param(key, id=key) for key in _DESCRIPTORS])
def test_descriptor_rejects_undeclared_states_and_terminal_exits(case: str) -> None:
    fields = {**_BASE, **_DESCRIPTORS[case]}
    if case == "valid":
        assert StateMachineDescriptor(**fields).initial == "open"
        return
    with pytest.raises(ValueError, match="Order"):
        StateMachineDescriptor(**fields)


_DECLARED = [
    pytest.param(edge, id=f"{edge.from_state}-{edge.event}")
    for edge in SUBMIT_AND_CANCEL.transitions
]


@pytest.mark.parametrize("edge", _DECLARED)
def test_next_state_follows_table_and_guards(edge: TransitionDecl) -> None:
    assert (
        next_state(SUBMIT_AND_CANCEL, edge.from_state, edge.event, _FACTS)
        == edge.to_state
    )


def test_next_state_follows_table_and_guards_undeclared_pair() -> None:
    with pytest.raises(StateTransitionRefused) as raised:
        next_state(SUBMIT_AND_CANCEL, "submitted", "submit", _FACTS)
    refusal = raised.value
    assert refusal.code == "STATE_TRANSITION_REFUSED"
    assert (refusal.machine, refusal.from_state, refusal.event, refusal.guard) == (
        "Requisition",
        "submitted",
        "submit",
        "",
    )
    with pytest.raises(StateTransitionRefused):
        next_state(SUBMIT_AND_CANCEL, "draft", "cancel", _FACTS)


@pytest.mark.parametrize("facts", [{}, {"line_count": 0}, {"unrelated": True}])
def test_next_state_follows_table_and_guards_guard_fails(facts: dict) -> None:
    with pytest.raises(StateTransitionRefused) as raised:
        next_state(SUBMIT_AND_CANCEL, "draft", "submit", facts)
    assert raised.value.guard == "has_lines"


@pytest.mark.parametrize("terminal", ["approved", "cancelled"])
def test_next_state_follows_table_and_guards_terminal_refuses_every_event(
    terminal: str,
) -> None:
    events = {edge.event for edge in SUBMIT_AND_CANCEL.transitions}
    for event in events:
        with pytest.raises(StateTransitionRefused):
            next_state(SUBMIT_AND_CANCEL, terminal, event, _FACTS)


def test_next_state_is_pure_and_guard_reads_only_declared_facts() -> None:
    facts = {"line_count": 1}
    assert next_state(SUBMIT_AND_CANCEL, "draft", "submit", facts) == "submitted"
    assert facts == {"line_count": 1}
    guard = GuardDecl("g", requires=("a", "b"))
    assert guard.holds({"a": 1, "b": True}) and not guard.holds({"a": 1})
    assert replace(SUBMIT_AND_CANCEL, initial="draft") == SUBMIT_AND_CANCEL
