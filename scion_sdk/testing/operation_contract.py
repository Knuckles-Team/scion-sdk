"""Check a connector's access contract against a pinned operation contract.

SCION-CONNECTOR-CONTROL-R037, R038 and R039. The SDK owns the rule (which class binds to
which served operation, and which source fields it carries); each connector repository
calls :func:`operation_contract_problems` from its own test with its ontology and a
recorded or synthetic response. The check reads only what it is given.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from scion_sdk.access_contract import AccessContractError, parse_access_contracts

__all__ = [
    "DEPLOYMENT",
    "PURCHASE_ORDER",
    "SUPPLIER",
    "UPTIME_MONITOR",
    "OperationContract",
    "operation_contract_problems",
]


@dataclass(frozen=True)
class OperationContract:
    """One class bound to one served operation with a pinned field list."""

    class_name: str
    operation: str
    key_field: str
    fields: tuple[str, ...]


SUPPLIER = OperationContract(
    "Supplier", "erpnext_agent_resource", "name", ("name", "status")
)
PURCHASE_ORDER = OperationContract(
    "PurchaseOrder",
    "erpnext_agent_resource",
    "name",
    ("name", "supplier", "status", "transaction_date"),
)
UPTIME_MONITOR = OperationContract(
    "UptimeMonitor",
    "uptime_kuma_service_health",
    "name",
    ("name", "status", "last_check"),
)
DEPLOYMENT = OperationContract(
    "Deployment", "k8s_list_services", "name", ("name", "available", "image")
)


def _local(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def operation_contract_problems(
    turtle: str,
    contract: OperationContract,
    records: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Problems with ``turtle`` and ``records`` against ``contract``; empty means sound.

    Codes: ``unparseable``, ``no_contract``, ``ambiguous_contract``, ``wrong_operation``,
    ``wrong_key_field``, ``unbound_field:<f>``, ``unpinned_field:<f>`` and
    ``record_missing_field:<f>`` (also ``no_records`` for an empty response).
    """
    try:
        parsed = parse_access_contracts(turtle)
    except AccessContractError:
        return ("unparseable",)
    found = [c for c in parsed if _local(c.class_iri) == contract.class_name]
    if not found:
        return ("no_contract",)
    if len(found) > 1:
        return ("ambiguous_contract",)
    (declared,) = found
    problems: list[str] = []
    if declared.operation != contract.operation:
        problems.append("wrong_operation")
    if declared.key_field != contract.key_field:
        problems.append("wrong_key_field")
    bound = {field for _prop, field in declared.predicates}
    problems += [f"unbound_field:{f}" for f in contract.fields if f not in bound]
    problems += [f"unpinned_field:{f}" for f in sorted(bound - set(contract.fields))]
    rows = list(records)
    if not rows:
        problems.append("no_records")
    for field in contract.fields:
        if any(field not in row for row in rows):
            problems.append(f"record_missing_field:{field}")
    return tuple(problems)
