"""A submit-and-cancel lifecycle fixture shared by the state-machine tests."""

from __future__ import annotations

from scion_sdk.state_machine import GuardDecl, StateMachineDescriptor, TransitionDecl

SUBMIT_AND_CANCEL = StateMachineDescriptor(
    name="Requisition",
    state_field="status",
    states=("draft", "submitted", "approved", "cancelled"),
    initial="draft",
    terminals=("approved", "cancelled"),
    transitions=(
        TransitionDecl("draft", "submit", "submitted", guard="has_lines"),
        TransitionDecl("draft", "discard", "cancelled"),
        TransitionDecl("submitted", "approve", "approved"),
        TransitionDecl("submitted", "cancel", "cancelled"),
    ),
    guards=(GuardDecl("has_lines", requires=("line_count",)),),
)
