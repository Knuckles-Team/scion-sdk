"""The data-prep step reads the engine declaration before preparing a field (R906)."""

from __future__ import annotations

from typing import Any

import pytest

from scion_sdk.data_prep import CleanPlan
from scion_sdk.data_prep.declaration_gate import (
    UndeclaredFieldError,
    plan_fields,
    read_declared_fields,
    require_declared,
)

DIGEST = "a" * 64


def _plan(*steps: dict[str, object]) -> CleanPlan:
    return CleanPlan.model_validate(
        {
            "schema_version": "1",
            "steps": list(steps),
            "profile": {"max_rows": 20, "max_columns": 8, "max_steps": 8},
            "invalid_row_disposition": "fail",
            "plan_ref": "plan:data-prep:v1",
            "policy_ref": "policy:data-prep:v1",
            "model_ref": "model:test:v1",
        }
    )


class _Client:
    def __init__(self, fields: list[str] | None) -> None:
        self.fields = fields
        self.calls: list[tuple[str, Any]] = []

    async def _send(self, method: str, params: Any, graph: Any, **_: Any) -> Any:
        self.calls.append((method, params))
        if self.fields is None:
            return {"content_digest": None, "declaration": None}
        return {
            "content_digest": DIGEST,
            "declaration": {
                "capability": "ingest",
                "field_refs": self.fields,
                "policy_digest": DIGEST,
            },
        }


@pytest.mark.spec("SCION-SOURCE-INGEST-R906")
async def test_undeclared_field_is_refused() -> None:
    plan = _plan(
        {"verb": "dedupe", "keys": ["id"]},
        {"verb": "null_policy", "columns": ["email"], "action": "reject"},
    )
    assert plan_fields(plan) == {"id", "email"}
    client = _Client(["id", "email"])
    declared = await read_declared_fields(client, tenant="t1", capability="ingest")
    require_declared(plan, declared)
    assert client.calls[0][0] == "DataPrepDeclarationRead"
    partial = await read_declared_fields(
        _Client(["id"]), tenant="t1", capability="ingest"
    )
    with pytest.raises(UndeclaredFieldError) as caught:
        require_declared(plan, partial)
    assert "email" not in str(caught.value) and caught.value.count == 1
    none = await read_declared_fields(_Client(None), tenant="t1", capability="ingest")
    with pytest.raises(UndeclaredFieldError):
        require_declared(plan, none)
