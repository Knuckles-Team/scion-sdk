"""Refuse to prepare a field the engine declaration does not list.

SCION-SOURCE-INGEST-R906. The runner step that builds a ``CleanPlan`` reads the
engine's declaration through ``DataPrepDeclarationRead`` and checks every column
the plan names against it before any preparation.
"""

from __future__ import annotations

from typing import Any

from medulla.generated.ingestion import (
    decode_data_prep_declaration_read,
    send_data_prep_declaration_read,
)

from scion_sdk.data_prep.models.plan import CleanPlan

__all__ = [
    "UndeclaredFieldError",
    "plan_fields",
    "read_declared_fields",
    "require_declared",
]


class UndeclaredFieldError(PermissionError):
    """The plan names a field the engine declaration does not list."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"{count} planned field(s) are not declared by the engine")


async def read_declared_fields(
    client: Any, *, tenant: str, capability: str
) -> frozenset[str]:
    """The declared field refs; an absent declaration lists none."""
    result = await send_data_prep_declaration_read(
        client, {"request": {"capability": capability, "tenant": tenant}}
    )
    view = decode_data_prep_declaration_read(result)
    if view.declaration is None:
        return frozenset()
    return frozenset(view.declaration.field_refs)


def plan_fields(plan: CleanPlan) -> frozenset[str]:
    """Every column a plan reads or writes."""
    names: set[str] = set()
    for step in plan.steps:
        verb = step.verb
        if verb == "null_policy":
            names.update(step.columns)  # type: ignore[union-attr]
        elif verb == "safe_cast":
            names.add(step.column)  # type: ignore[union-attr]
        elif verb == "dedupe":
            names.update(step.keys)  # type: ignore[union-attr]
        elif verb == "fill_nulls":
            names.update(step.fills)  # type: ignore[union-attr]
    return frozenset(names)


def require_declared(plan: CleanPlan, declared: frozenset[str]) -> None:
    """Refuse a plan that names any field outside ``declared``; names stay out of the message."""
    undeclared = plan_fields(plan) - declared
    if undeclared:
        raise UndeclaredFieldError(len(undeclared))
