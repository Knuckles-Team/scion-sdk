"""Typed operation contracts for connector-served classes (R037, R038, R039)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from scion_sdk.testing.operation_contract import (
    DEPLOYMENT,
    PURCHASE_ORDER,
    SUPPLIER,
    UPTIME_MONITOR,
    OperationContract,
    operation_contract_problems,
)

_HEAD = """
@prefix ac: <https://knuckles-team.github.io/ontology/access#> .
@prefix ex: <https://example.invalid/fake#> .
"""


def _turtle(*contracts: OperationContract, drop: str = "", operation: str = "") -> str:
    out = [_HEAD]
    for contract in contracts:
        name = contract.class_name
        out.append(
            f"ex:{name} a <http://www.w3.org/2002/07/owl#Class> .\n"
            f"ex:{name}Access a ac:AccessContract ; ac:classIri ex:{name} ;\n"
            f'  ac:sourceId "fake" ; ac:entity "{name.lower()}" ;\n'
            f'  ac:keyField "{contract.key_field}" ; ac:accessKind "mcp_tool" ;\n'
            f'  ac:operation "{operation or contract.operation}" .\n'
        )
        for field in contract.fields:
            if field == drop:
                continue
            out.append(
                f"ex:{name}{field} a ac:PropertyBinding ; ac:contract ex:{name}Access ;"
                f' ac:property ex:{field} ; ac:field "{field}" .\n'
            )
    return "".join(out)


def _records(contract: OperationContract, count: int = 2) -> list[dict[str, Any]]:
    return [{field: f"{field}-{i}" for field in contract.fields} for i in range(count)]


async def _served(contract: OperationContract) -> list[dict[str, Any]]:
    """The records a synthetic provider returns through a real MCP tool."""
    server = FastMCP("synthetic")
    rows = _records(contract)

    @server.tool(name=contract.operation)
    def operation() -> list[dict[str, Any]]:
        return rows

    async with Client(server) as client:
        result = await client.call_tool(contract.operation, {})
    return list(result.data)


def _negative_cases(
    contract: OperationContract, records: Sequence[dict[str, Any]]
) -> None:
    dropped = contract.fields[-1]
    problems = operation_contract_problems(
        _turtle(contract, drop=dropped), contract, records
    )
    assert problems == (f"unbound_field:{dropped}",)
    assert operation_contract_problems(
        _turtle(contract, operation="other_tool"), contract, records
    ) == ("wrong_operation",)
    thin = [{k: v for k, v in r.items() if k != dropped} for r in records]
    assert operation_contract_problems(_turtle(contract), contract, thin) == (
        f"record_missing_field:{dropped}",
    )
    assert operation_contract_problems(_turtle(contract), contract, []) == (
        "no_records",
    )
    assert operation_contract_problems("not turtle at all", contract, records) == (
        "unparseable",
    )
    other = SUPPLIER if contract is not SUPPLIER else DEPLOYMENT
    assert operation_contract_problems(_turtle(other), contract, records) == (
        "no_contract",
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R037")
def test_supplier_and_purchase_order_fields_match_recorded_fixture(tmp_path) -> None:
    fixture = tmp_path / "supplier_purchase_order.json"
    recorded = {
        "Supplier": _records(SUPPLIER),
        "PurchaseOrder": _records(PURCHASE_ORDER),
    }
    fixture.write_text(json.dumps(recorded, sort_keys=True), encoding="utf-8")
    loaded = json.loads(fixture.read_text(encoding="utf-8"))
    both = _turtle(SUPPLIER, PURCHASE_ORDER)
    assert SUPPLIER.operation == PURCHASE_ORDER.operation == "erpnext_agent_resource"
    assert PURCHASE_ORDER.fields == ("name", "supplier", "status", "transaction_date")
    for contract in (SUPPLIER, PURCHASE_ORDER):
        assert (
            operation_contract_problems(both, contract, loaded[contract.class_name])
            == ()
        )
        _negative_cases(contract, loaded[contract.class_name])
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(fixture.read_bytes()).hexdigest()
    second = (
        "ex:SupplierAccess2 a ac:AccessContract ; ac:classIri ex:Supplier ;\n"
        '  ac:sourceId "fake" ; ac:entity "supplier" ; ac:keyField "name" ;\n'
        '  ac:accessKind "mcp_tool" ; ac:operation "erpnext_agent_resource" .\n'
    )
    ambiguous = both + second
    assert operation_contract_problems(ambiguous, SUPPLIER, loaded["Supplier"]) == (
        "ambiguous_contract",
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R038")
async def test_served_health_contract() -> None:
    records = await _served(UPTIME_MONITOR)
    assert records == _records(UPTIME_MONITOR)
    assert (
        operation_contract_problems(_turtle(UPTIME_MONITOR), UPTIME_MONITOR, records)
        == ()
    )
    _negative_cases(UPTIME_MONITOR, records)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R039")
async def test_deployment_contract_matches_served_fixture() -> None:
    records = await _served(DEPLOYMENT)
    assert records == _records(DEPLOYMENT)
    assert operation_contract_problems(_turtle(DEPLOYMENT), DEPLOYMENT, records) == ()
    _negative_cases(DEPLOYMENT, records)
