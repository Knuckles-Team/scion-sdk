"""The registry of connector transports: one registration per admitted protocol.

SCION-CONNECTOR-CONTROL-R069. A protocol in ``ACCESS_KINDS`` is declared; it is
admitted only when a registration names the SDK module that implements it.
Admission and certification read this one table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from scion_sdk.contract_fields import ACCESS_KINDS
from scion_sdk.transports.contract import TransportContract

__all__ = [
    "TransportRegistration",
    "TransportRegistryError",
    "check_declared_transports",
    "register_transport",
    "registered_transport",
    "transport_matrix",
]


class TransportRegistryError(ValueError):
    """A registry refusal; ``code`` is closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class TransportRegistration:
    """One protocol, its capability contract and the SDK module implementing it."""

    contract: TransportContract
    module: str


_REGISTRY: dict[str, TransportRegistration] = {}


def register_transport(registration: TransportRegistration) -> None:
    """Add one registration.

    Raises:
        TransportRegistryError: ``DUPLICATE_TRANSPORT``; the registry is unchanged.
    """
    protocol = registration.contract.protocol
    if protocol in _REGISTRY:
        raise TransportRegistryError(
            "DUPLICATE_TRANSPORT", f"{protocol} is already registered"
        )
    _REGISTRY[protocol] = registration


def registered_transport(protocol: str) -> TransportRegistration | None:
    """The registration of ``protocol``, or ``None`` when it is declared only."""
    return _REGISTRY.get(protocol)


def _default_table() -> tuple[TransportRegistration, ...]:
    def entry(
        protocol: str, module: str, auth: str, paging: str, pushdown: set[str]
    ) -> TransportRegistration:
        contract = TransportContract(
            protocol, auth, paging=paging, pushdown=frozenset(pushdown)
        )
        return TransportRegistration(contract, module)

    return (
        entry(
            "http_endpoint",
            "scion_sdk.http.client",
            "api_key",
            "cursor",
            {"filter", "project", "sort", "limit"},
        ),
        entry(
            "graphql_query",
            "scion_sdk.transports.graphql",
            "bearer",
            "cursor",
            {"filter", "project", "limit"},
        ),
        entry(
            "mcp_tool",
            "scion_sdk.transports.mcp",
            "bearer",
            "cursor",
            {"filter", "limit"},
        ),
        entry("a2a_skill", "scion_sdk.a2a.client", "bearer", "none", {"filter"}),
        entry(
            "virtual_graph",
            "scion_sdk.source_connectors.universal",
            "password",
            "cursor",
            {"filter", "project", "join", "aggregate", "limit", "time_range"},
        ),
    )


for _registration in _default_table():
    register_transport(_registration)


def _protocol_defect(protocol: str, where: str) -> str | None:
    if protocol not in ACCESS_KINDS:
        return f"ACCESS_KIND_UNKNOWN: {where}: {protocol!r} is not a declared protocol"
    if protocol not in _REGISTRY:
        return (
            f"TRANSPORT_UNIMPLEMENTED: {where}: no transport registered for {protocol}"
        )
    return None


def check_declared_transports(
    descriptors: Iterable[Mapping[str, Any]], contracts: Iterable[Any]
) -> tuple[str, ...]:
    """Every ``CODE: detail`` defect of the declared protocols and pushdowns.

    ``descriptors`` are the ``connector://resources`` entries; ``contracts``
    expose ``access_kind``, ``pushdown`` and ``contract_iri``.
    """
    defects: list[str] = []
    for descriptor in descriptors:
        where = f"resource {descriptor.get('name')}"
        protocol = str(descriptor.get("access_kind", "mcp_tool"))
        defect = _protocol_defect(protocol, where)
        if defect is not None:
            defects.append(defect)
    for contract in contracts:
        where = f"contract {contract.contract_iri}"
        defect = _protocol_defect(contract.access_kind, where)
        if defect is not None:
            defects.append(defect)
            continue
        extra = sorted(
            set(contract.pushdown) - _REGISTRY[contract.access_kind].contract.pushdown
        )
        if extra:
            defects.append(
                f"PUSHDOWN_UNSUPPORTED: {where}: {', '.join(extra)} "
                f"outside the {contract.access_kind} pushdown"
            )
    return tuple(defects)


def transport_matrix(protocols: Iterable[str]) -> tuple[dict[str, Any], ...]:
    """One object per distinct registered protocol, sorted by protocol."""
    rows: list[dict[str, Any]] = []
    for protocol in sorted(set(protocols)):
        registration = _REGISTRY.get(protocol)
        if registration is None:
            continue
        contract = registration.contract
        rows.append(
            {
                "protocol": protocol,
                "module": registration.module,
                "auth_model": contract.auth_model,
                "paging": contract.paging,
                "streaming": contract.streaming,
                "pushdown": sorted(contract.pushdown),
            }
        )
    return tuple(rows)
