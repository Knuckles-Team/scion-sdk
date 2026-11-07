"""A descriptor renders as classes under the core vocabulary (SCION-GOVERNED-WRITEBACK-R007.4)."""

from __future__ import annotations

import pytest
from state_machine_fixtures import SUBMIT_AND_CANCEL

from scion_sdk.state_machine import (
    MACHINE_NS,
    StateMachineDescriptor,
    render_state_machine_turtle,
)
from scion_sdk.turtle_reader import parse_graph

pytestmark = [pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R007.4")]


_KG = "http://knuckles.team/kg#"
_SUB = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


def test_generated_classes_extend_core_and_cover_every_transition() -> None:
    text = render_state_machine_turtle(SUBMIT_AND_CANCEL)
    assert "owl:imports" not in text
    assert text == render_state_machine_turtle(SUBMIT_AND_CANCEL)
    graph = parse_graph(text)
    ns = f"{MACHINE_NS}Requisition#"
    assert graph[ns + "Requisition"][_SUB] == [_KG + "StateMachine"]
    for state in SUBMIT_AND_CANCEL.states:
        assert _KG + "State" in graph[f"{ns}State_{state}"][_SUB]
    for state in SUBMIT_AND_CANCEL.terminals:
        assert _KG + "TerminalState" in graph[f"{ns}State_{state}"][_SUB]
    for event in {edge.event for edge in SUBMIT_AND_CANCEL.transitions}:
        assert graph[f"{ns}Event_{event}"][_SUB] == [_KG + "Event"]
    assert graph[ns + "Guard_has_lines"][_SUB] == [_KG + "Guard"]
    assert graph[ns + "RequisitionTransitionRefused"][_SUB] == [
        _KG + "TransitionRefused"
    ]
    for edge in SUBMIT_AND_CANCEL.transitions:
        suffix = f"_{edge.guard}" if edge.guard else ""
        key = f"{ns}Transition_{edge.from_state}_{edge.event}_{edge.to_state}{suffix}"
        node = graph[key]
        assert node[_SUB] == [_KG + "Transition"]
        assert node[_KG + "fromState"] == [f"{ns}State_{edge.from_state}"]
        assert node[_KG + "toState"] == [f"{ns}State_{edge.to_state}"]
        assert node[_KG + "firedBy"] == [f"{ns}Event_{edge.event}"]
        assert node.get(_KG + "guardedBy", []) == (
            [f"{ns}Guard_{edge.guard}"] if edge.guard else []
        )


def test_a_descriptor_that_names_a_state_twice_fails() -> None:
    with pytest.raises(ValueError, match="twice"):
        StateMachineDescriptor(
            name="Dup", state_field="s", states=("a", "a"), initial="a"
        )
