"""The lifecycle and breaker vocabulary (SCION-CONNECTOR-CONTROL-R917.5, R918.3)."""

from __future__ import annotations

from importlib import resources

import pytest

from scion_sdk.connector_lifecycle import EDGES, LifecycleEvent, LifecycleState
from scion_sdk.turtle_reader import RDF_TYPE, parse_graph

pytestmark = [
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R917.5"),
    pytest.mark.spec("SCION-CONNECTOR-CONTROL-R918.3"),
]


_KG = "http://knuckles.team/kg#"
_CL = "https://knuckles-team.github.io/scion-sdk/lifecycle#"
_CB = "https://knuckles-team.github.io/scion-sdk/breaker#"
_SUBCLASS = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
_CORE = {
    "StateMachine",
    "State",
    "Transition",
    "Event",
    "Guard",
    "TransitionRefused",
    "TerminalState",
}


def _text() -> str:
    path = resources.files("scion_sdk") / "ontology" / "connector_lifecycle.ttl"
    return path.read_text(encoding="utf-8")


def _parents(graph, iri: str) -> list[str]:
    return [str(o) for o in graph[iri][_SUBCLASS]]


def _camel(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_"))


def test_turtle_matches_the_table_and_extends_core() -> None:
    text = _text()
    assert "owl:imports" not in text
    graph = parse_graph(text)
    assert _parents(graph, _CL + "ConnectorLifecycle") == [_KG + "StateMachine"]
    for state in LifecycleState:
        assert _KG + "State" in _parents(graph, _CL + state.value)
    assert _KG + "TerminalState" in _parents(graph, _CL + "Stopped")
    for event in LifecycleEvent:
        assert _parents(graph, f"{_CL}{_camel(event.value)}Event") == [_KG + "Event"]
    for guard in ("FailuresAtThreshold", "FirstProvenCycle"):
        assert _parents(graph, _CL + guard) == [_KG + "Guard"]
    refusal = _CL + "LifecycleTransitionRefused"
    assert _parents(graph, refusal) == [_KG + "TransitionRefused"]
    for edge in EDGES:
        node = graph[f"{_CL}Edge_{edge.id}"]
        assert node[_SUBCLASS] == [_KG + "Transition"]
        assert node[_KG + "fromState"] == [_CL + edge.source.value]
        assert node[_KG + "toState"] == [_CL + edge.target.value]
        assert node[_KG + "firedBy"] == [f"{_CL}{_camel(edge.event.value)}Event"]
        assert node.get(_KG + "guardedBy", []) == (
            [_CL + edge.guard] if edge.guard else []
        )
    edge_nodes = [s for s in graph if s.startswith(_CL + "Edge_")]
    assert len(edge_nodes) == len(EDGES)


def test_breaker_classes_extend_core() -> None:
    graph = parse_graph(_text())
    assert _parents(graph, _CB + "CircuitBreaker") == [_KG + "StateMachine"]
    for state in ("Closed", "Open", "HalfOpen"):
        assert _parents(graph, _CB + state) == [_KG + "State"]
    for subject, props in graph.items():
        for parent in props.get(_SUBCLASS, []):
            if str(parent).startswith(_KG):
                assert str(parent)[len(_KG) :] in _CORE
        assert RDF_TYPE in props or subject.startswith(_CL) or subject.startswith(_CB)
