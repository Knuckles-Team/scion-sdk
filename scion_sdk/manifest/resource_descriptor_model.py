"""Validated resource descriptor model and serialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from scion_sdk.contract_fields import ACCESS_KINDS
from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.transports.registry import registered_transport

_RESOURCE_KINDS = frozenset({"data", "ontology", "shapes", "skill", "seed", "manifest"})
#: Dialects and capabilities a ``virtual_graph`` resource declares (R912).
VIRTUAL_GRAPH_DIALECTS = frozenset(
    {"mssql", "oracle", "db2", "mongodb", "documentdb", "snowflake", "bigquery"}
)
VIRTUAL_GRAPH_CAPABILITIES = frozenset(
    {"projection", "predicate", "limit", "join", "aggregate", "order", "capture"}
)
_RESOURCE_PAGINATION_MODES = frozenset({"none", "cursor", "offset", "page"})


class ResourceDeclarationError(ValueError):
    """A declared data resource is refused; ``code`` names the reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class FieldBinding:
    """One response field and the ontology property it carries."""

    name: str
    type: str = "string"
    ontology_property: str | None = None
    mapping_status: str = "unmapped"

    def to_json(self) -> dict[str, Any]:
        """The field as a JSON object."""
        return {
            "name": self.name,
            "type": self.type,
            "ontology_property": self.ontology_property,
            "mapping_status": self.mapping_status,
        }


@dataclass(frozen=True)
class ResourceDescriptor:
    """One data resource of the connector and the contract to read it live."""

    name: str
    uri: str
    ontology_class: str | None
    mapping_status: str
    operation: str
    key_field: str
    pagination: str
    origin: str
    fields: tuple[FieldBinding, ...] = ()
    arguments: Mapping[str, str] = field(default_factory=dict)
    pagination_param: str | None = None
    get_operation: str | None = None
    access_kind: str = "mcp_tool"
    kind: str = "data"
    read_only: bool = True
    owner: str | None = None
    credential_ref: str | None = None
    records_path: str | None = None
    next_cursor_path: str | None = None
    page_size: int | None = None
    page_size_param: str | None = None
    start_page: int = 1
    start_offset: int = 0
    completion: str | None = None
    dialect: str | None = None
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse unsafe values even when callers construct a descriptor directly."""
        if self.kind not in _RESOURCE_KINDS:
            raise ResourceDeclarationError(
                "RESOURCE_KIND_UNKNOWN", "unknown resource kind"
            )
        refusal = _validate(self.to_json(), None)
        if refusal is not None:
            raise refusal

    @classmethod
    def from_json(cls, document: Mapping[str, Any]) -> ResourceDescriptor:
        """Read the SDK's serialized descriptor and validate its contract."""
        values = dict(document)
        values["capabilities"] = tuple(values.get("capabilities") or ())
        values["fields"] = tuple(
            FieldBinding(**field) for field in values.get("fields", ())
        )
        return cls(**values)

    def to_json(self) -> dict[str, Any]:
        """The descriptor as a JSON object."""
        return {
            "name": self.name,
            "uri": self.uri,
            "kind": self.kind,
            "origin": self.origin,
            "ontology_class": self.ontology_class,
            "mapping_status": self.mapping_status,
            "fields": [binding.to_json() for binding in self.fields],
            "access_kind": self.access_kind,
            "operation": self.operation,
            "get_operation": self.get_operation,
            "arguments": dict(self.arguments),
            "key_field": self.key_field,
            "pagination": self.pagination,
            "pagination_param": self.pagination_param,
            "records_path": self.records_path,
            "next_cursor_path": self.next_cursor_path,
            "page_size": self.page_size,
            "page_size_param": self.page_size_param,
            "start_page": self.start_page,
            "start_offset": self.start_offset,
            "completion": self.completion,
            "dialect": self.dialect,
            "capabilities": list(self.capabilities),
            "read_only": self.read_only,
            "owner": self.owner,
            "credential_ref": self.credential_ref,
        }


def _validate_paging(entry: Mapping[str, Any]) -> ResourceDeclarationError | None:
    """The refusal for one merged declaration, or ``None`` when it is sound."""
    name = entry.get("name")
    completion = entry.get("completion")
    if completion is not None and (
        not isinstance(completion, str)
        or completion not in {"short_page", "cursor_null"}
    ):
        return ResourceDeclarationError(
            "PAGINATION_INVALID", f"{name}: invalid completion"
        )
    return _validate_paging_paths(entry) or _validate_paging_numbers(entry)


def _validate_paging_paths(entry: Mapping[str, Any]) -> ResourceDeclarationError | None:
    name = entry.get("name")
    for key in (
        "records_path",
        "next_cursor_path",
        "pagination_param",
        "page_size_param",
    ):
        value = entry.get(key)
        if value is not None and (
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or any(ord(character) < 32 for character in value)
            or any(not part for part in value.split("."))
        ):
            return ResourceDeclarationError(
                "PAGINATION_INVALID", f"{name}: invalid {key}"
            )
    return None


def _validate_paging_numbers(
    entry: Mapping[str, Any],
) -> ResourceDeclarationError | None:
    name = entry.get("name")
    for key, maximum in (
        ("page_size", 1000),
        ("start_page", 1_000_000),
        ("start_offset", 1_000_000),
    ):
        value = entry.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value > maximum
            or (key == "page_size" and value == 0)
        ):
            return ResourceDeclarationError(
                "PAGINATION_INVALID", f"{name}: invalid {key}"
            )
    return None


def _validate_dialect(entry: Mapping[str, Any]) -> ResourceDeclarationError | None:
    """A ``virtual_graph`` names a known dialect and known capabilities (R912)."""
    name = entry.get("name")
    dialect = entry.get("dialect")
    capabilities = entry.get("capabilities") or ()
    if entry.get("access_kind", "mcp_tool") != "virtual_graph":
        if dialect is not None or capabilities:
            return ResourceDeclarationError(
                "CONTRACT_DIALECT_UNKNOWN", f"{name}: dialect needs a virtual_graph"
            )
        return None
    if dialect not in VIRTUAL_GRAPH_DIALECTS or not set(capabilities) <= (
        VIRTUAL_GRAPH_CAPABILITIES
    ):
        return ResourceDeclarationError(
            "CONTRACT_DIALECT_UNKNOWN", f"{name}: unknown dialect or capability"
        )
    return None


def _validate(
    entry: Mapping[str, Any], classes: frozenset[str] | None
) -> ResourceDeclarationError | None:
    """The refusal for one merged declaration, or None when it is sound."""
    name = entry.get("name")
    paging_refusal = _validate_paging(entry)
    if paging_refusal is not None:
        return paging_refusal
    kind = entry.get("access_kind", "mcp_tool")
    if kind not in ACCESS_KINDS:
        return ResourceDeclarationError(
            "ACCESS_KIND_UNKNOWN", f"{name}: unknown access_kind"
        )
    if registered_transport(kind) is None:
        return ResourceDeclarationError(
            "TRANSPORT_UNIMPLEMENTED", f"{name}: no transport registered for {kind}"
        )
    if entry.get("pagination") not in _RESOURCE_PAGINATION_MODES:
        return ResourceDeclarationError(
            "PAGINATION_UNDECLARED",
            f"{name}: pagination must be one of cursor, page, offset or none",
        )
    if classes is not None and entry.get("ontology_class") not in classes:
        return ResourceDeclarationError(
            "RESOURCE_UNMAPPED",
            f"{name}: {entry.get('ontology_class')!r} is not declared in the ontology",
        )
    dialect_refusal = _validate_dialect(entry)
    if dialect_refusal is not None:
        return dialect_refusal
    ref = entry.get("credential_ref")
    if ref is not None:
        try:
            parse_secret_reference(ref)
        except SecretReferenceError:
            raise ResourceDeclarationError(
                "SECRET_IN_CONTRACT", f"{name}: credential_ref must be a reference"
            ) from None
    return None
