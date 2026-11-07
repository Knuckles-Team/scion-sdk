"""The shared base of every typed state-machine refusal (organization constitution Article XXXI).

Each machine defines its own refusal class beside its transition table and
gives it a stable ``code``. The base carries the fields they share, so no
machine hand-rolls them. Each subclass maps to the core ``TransitionRefused``
ontology class.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = ["MachineTransitionRefused"]


class MachineTransitionRefused(Exception):
    """An event was refused in a state that does not declare it.

    Attributes:
        code: The stable refusal code of the machine.
        machine: The machine name.
        from_state: The state the machine was in; it stays there.
        event: The refused event.
        reason: A short human reason, never a secret or upstream text.
    """

    code: ClassVar[str] = "TRANSITION_REFUSED"
    machine_name: ClassVar[str] = ""

    def __init__(
        self,
        from_state: str,
        event: str,
        reason: str = "",
        *,
        machine: str | None = None,
    ) -> None:
        self.machine = machine if machine is not None else self.machine_name
        self.from_state = from_state
        self.event = event
        self.reason = reason or f"{event} is not declared in {from_state}"
        super().__init__(
            f"{self.code}: {self.machine} {self.from_state} refuses {self.event}: "
            f"{self.reason}"
        )
