"""Declare the lifecycle of an external-system record (SCION-GOVERNED-WRITEBACK-R007).

A connector declares the lifecycle of one kind of external record as a
:class:`StateMachineDescriptor`: states, events, transitions, guards and
terminals. :func:`next_state` is the one pure function that decides whether an
event is legal. Any pair the descriptor does not declare is refused with
:class:`StateTransitionRefused`. :func:`render_state_machine_turtle` writes the
machine as classes that extend the core state-machine classes, with no
``owl:imports``. Connectors declare their machines here and do not hand-roll
status checks.

A guard holds when every fact it ``requires`` is present in the supplied
facts and truthy, such as a draft flag set or a link count above zero. A guard
reads only its declared facts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from scion_sdk.surface_contracts import CORE_NS
from scion_sdk.transition_refused import MachineTransitionRefused

__all__ = [
    "MACHINE_NS",
    "GuardDecl",
    "StateMachineDescriptor",
    "StateTransitionRefused",
    "TransitionDecl",
    "next_state",
    "render_state_machine_turtle",
]

#: Namespace root of rendered machines; one fragment namespace per machine name.
MACHINE_NS = "https://knuckles-team.github.io/scion-sdk/machine/"
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class StateTransitionRefused(MachineTransitionRefused):
    """A declared machine refused an event before any external call.

    Carries ``machine`` (the descriptor name), ``from_state``, ``event`` and
    ``guard`` (the failed guard name, or empty). Its code is
    ``STATE_TRANSITION_REFUSED``. It maps to the core ``TransitionRefused`` class.
    """

    code: ClassVar[str] = "STATE_TRANSITION_REFUSED"

    def __init__(
        self, machine: str, from_state: str, event: str, guard: str = ""
    ) -> None:
        self.guard = guard
        reason = f"guard {guard} does not hold" if guard else ""
        super().__init__(from_state, event, reason, machine=machine)


def _tuple(values: Iterable[Any]) -> tuple[Any, ...]:
    return (values,) if isinstance(values, str) else tuple(values)


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _check_name(kind: str, value: str) -> None:
    if not isinstance(value, str) or not _NAME.match(value):
        raise ValueError(f"{kind} {value!r} must match {_NAME.pattern}")


@dataclass(frozen=True)
class GuardDecl:
    """A named condition over declared facts of the external record.

    ``requires`` lists the fact names the guard reads. It holds when each is
    present and truthy.
    """

    name: str
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check_name("guard", self.name)
        _set(self, "requires", tuple(str(r) for r in _tuple(self.requires)))

    def holds(self, facts: Mapping[str, Any]) -> bool:
        """Whether every required fact is present and truthy."""
        return all(facts.get(name) for name in self.requires)


@dataclass(frozen=True)
class TransitionDecl:
    """One declared edge; ``guard`` optionally names a :class:`GuardDecl`."""

    from_state: str
    event: str
    to_state: str
    guard: str = ""

    def __post_init__(self) -> None:
        _check_name("event", self.event)


@dataclass(frozen=True)
class StateMachineDescriptor:
    """The declared lifecycle of one external record kind.

    Attributes:
        name: The machine name.
        state_field: The one attribute of the external record that holds the state.
        states: Every state, each declared once.
        initial: The state of a new record.
        terminals: The states with no outgoing edge.
        transitions: The declared edges; every other pair is refused.
        guards: The guards the transitions name.

    Raises:
        ValueError: a state is declared twice, a transition names an undeclared
            state or guard, a terminal has an outgoing edge, or ``initial`` is
            not a declared state.
    """

    name: str
    state_field: str
    states: tuple[str, ...]
    initial: str
    terminals: tuple[str, ...] = ()
    transitions: tuple[TransitionDecl, ...] = ()
    guards: tuple[GuardDecl, ...] = ()

    def __post_init__(self) -> None:
        for attr in ("states", "terminals", "transitions", "guards"):
            _set(self, attr, _tuple(getattr(self, attr)))
        _check_name("machine", self.name)
        for state in self.states:
            _check_name("state", state)
        self._validate()

    def _validate(self) -> None:
        declared = set(self.states)
        if len(declared) != len(self.states):
            raise ValueError(f"{self.name}: a state is declared twice")
        if self.initial not in declared:
            raise ValueError(f"{self.name}: initial {self.initial!r} is not declared")
        undeclared = set(self.terminals) - declared
        if undeclared:
            raise ValueError(f"{self.name}: terminal {sorted(undeclared)} not declared")
        guards = {guard.name for guard in self.guards}
        if len(guards) != len(self.guards):
            raise ValueError(f"{self.name}: a guard is declared twice")
        for edge in self.transitions:
            if edge.from_state not in declared or edge.to_state not in declared:
                raise ValueError(f"{self.name}: {edge} names an undeclared state")
            if edge.from_state in self.terminals:
                raise ValueError(f"{self.name}: terminal {edge.from_state} has an exit")
            if edge.guard and edge.guard not in guards:
                raise ValueError(f"{self.name}: guard {edge.guard!r} is not declared")

    def guard(self, name: str) -> GuardDecl:
        """The declared guard called ``name``."""
        return next(guard for guard in self.guards if guard.name == name)


def next_state(
    descriptor: StateMachineDescriptor,
    current: str,
    event: str,
    facts: Mapping[str, Any] | None = None,
) -> str:
    """The target state of ``event`` in ``current``, or a typed refusal.

    Pure: reads only ``facts`` and makes no API call.

    Raises:
        StateTransitionRefused: no declared edge fits, or its guard fails (the
            refusal names the first failed guard).
    """
    facts = facts or {}
    failed = ""
    for edge in descriptor.transitions:
        if edge.from_state != current or edge.event != event:
            continue
        if not edge.guard or descriptor.guard(edge.guard).holds(facts):
            return edge.to_state
        failed = failed or edge.guard
    raise StateTransitionRefused(descriptor.name, current, event, failed)


def render_state_machine_turtle(descriptor: StateMachineDescriptor) -> str:
    """Render ``descriptor`` as deterministic Turtle classes with no imports.

    The machine is a subclass of ``StateMachine``; each state, transition,
    event, guard and the refusal is a subclass of ``State``, ``Transition``,
    ``Event``, ``Guard`` and ``TransitionRefused``. Terminal states also extend
    ``TerminalState``.
    """
    name = descriptor.name
    lines = [
        f"# State machine {name} (SCION-GOVERNED-WRITEBACK-R007.4).",
        f"@prefix m:    <{MACHINE_NS}{name}#> .",
        f"@prefix kg:   <{CORE_NS}> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix owl:  <http://www.w3.org/2002/07/owl#> .",
        "",
        f"m:{name} a owl:Class ; rdfs:subClassOf kg:StateMachine ;",
        f'    rdfs:comment "State field {descriptor.state_field}; initial {descriptor.initial}." .',
    ]
    for state in descriptor.states:
        parents = "kg:State"
        if state in descriptor.terminals:
            parents += ", kg:TerminalState"
        lines.append(f"m:State_{state} a owl:Class ; rdfs:subClassOf {parents} .")
    for event in sorted({edge.event for edge in descriptor.transitions}):
        lines.append(f"m:Event_{event} a owl:Class ; rdfs:subClassOf kg:Event .")
    for guard in descriptor.guards:
        lines.append(f"m:Guard_{guard.name} a owl:Class ; rdfs:subClassOf kg:Guard .")
    lines.append(
        f"m:{name}TransitionRefused a owl:Class ; rdfs:subClassOf kg:TransitionRefused ;"
    )
    lines.append('    rdfs:comment "Code STATE_TRANSITION_REFUSED." .')
    for edge in descriptor.transitions:
        suffix = f"_{edge.guard}" if edge.guard else ""
        parts = [
            f"m:Transition_{edge.from_state}_{edge.event}_{edge.to_state}{suffix}"
            " a owl:Class ; rdfs:subClassOf kg:Transition",
            f"kg:fromState m:State_{edge.from_state}",
            f"kg:toState m:State_{edge.to_state}",
            f"kg:firedBy m:Event_{edge.event}",
        ]
        if edge.guard:
            parts.append(f"kg:guardedBy m:Guard_{edge.guard}")
        lines.append(" ;\n    ".join(parts) + " .")
    return "\n".join(lines) + "\n"
