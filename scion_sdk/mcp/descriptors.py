"""Operation descriptors and the scope registry a connector serves (R045.12).

Every served operation declares one :class:`OperationDescriptor` under the tool ``_meta``
key :data:`OPERATION_META_KEY`; the server's scope registry is served under ``scopes``
in ``connector://resources``. Both are built from server code when read. No contract
file is generated, committed, packaged or read. The engine's own descriptors map to
the same model through :func:`descriptor_from_engine_row`; fetching them needs the
engine's ``DescribeContract`` read, guarded by :mod:`scion_sdk.engine_capability`.
"""

from __future__ import annotations

import weakref
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.engine_capability import require_engine_capability
from scion_sdk.mcp._tool_surface_base import registered_tools

__all__ = [
    "OPERATION_META_KEY",
    "OperationDescriptor",
    "ScopeRegistryEntry",
    "descriptor_from_engine_row",
    "fetch_engine_contract",
    "register_operation_descriptors",
    "scopes_body",
]

#: Not ``eg.annotations``: that key is the closed pack-annotation schema (extra fields
#: are refused), so the operation descriptor travels under its own key.
OPERATION_META_KEY = "eg.operation"
_MAX_PAGES = 64  # bound on engine pages followed in one fetch

Stability = Literal["stable", "internal", "deprecated"]
Idempotency = Literal["natural", "key-required", "none"]
ScopeClass = Literal["user", "domain", "service-only", "approver", "admin"]


class OperationDescriptor(BaseModel):
    """What one operation needs and promises. The tool description is the summary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: str = Field(min_length=1)
    stability: Stability
    idempotency: Idempotency
    replay_class: str = Field(min_length=1)
    audited: bool
    mutates: bool


class ScopeRegistryEntry(BaseModel):
    """One scope the server checks, with who owns and approves it."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    scope: str = Field(min_length=1)
    scope_class: ScopeClass = Field(alias="class")
    owner: str = Field(min_length=1)
    approver_group: str | None = None


_SCOPES: weakref.WeakKeyDictionary[Any, tuple[ScopeRegistryEntry, ...]] = (
    weakref.WeakKeyDictionary()
)


def register_operation_descriptors(
    mcp: Any,
    descriptors: Mapping[str, OperationDescriptor],
    scopes: Sequence[ScopeRegistryEntry],
) -> None:
    """Attach one descriptor to every served tool and record the scope registry.

    Refuses a descriptor for an unregistered tool, a served tool without one, a scope
    the registry does not list, and a duplicate scope entry.
    """
    tools = registered_tools(mcp)
    unknown = sorted(set(descriptors) - set(tools))
    if unknown:
        raise ValueError(f"descriptors name unregistered tools: {unknown}")
    missing = sorted(set(tools) - set(descriptors))
    if missing:
        raise ValueError(f"served tools declare no descriptor: {missing}")
    listed = [entry.scope for entry in scopes]
    if len(listed) != len(set(listed)):
        raise ValueError("the scope registry lists a scope twice")
    undeclared = sorted({d.scope for d in descriptors.values()} - set(listed))
    if undeclared:
        raise ValueError(f"descriptors use scopes the registry lacks: {undeclared}")
    for name, descriptor in descriptors.items():
        tool = tools[name]
        tool.meta = {**(tool.meta or {}), OPERATION_META_KEY: descriptor.model_dump()}
    _SCOPES[mcp] = tuple(sorted(scopes, key=lambda entry: entry.scope))


def scopes_body(mcp: Any) -> list[dict[str, Any]] | None:
    """The registry as served under ``scopes``; ``None`` when none is registered."""
    entries = _SCOPES.get(mcp)
    if entries is None:
        return None
    return [entry.model_dump(by_alias=True) for entry in entries]


def descriptor_from_engine_row(row: Mapping[str, Any]) -> OperationDescriptor:
    """Map one engine method descriptor to the SDK model.

    The engine's ``policy.authz_action`` is the scope; ``policy.idempotent`` is a
    natural idempotency, a mutating non-idempotent method needs a key, anything else
    has none.
    """
    policy = row["policy"]
    mutates = bool(policy["mutates"])
    if policy["idempotent"]:
        idempotency: Idempotency = "natural"
    elif mutates:
        idempotency = "key-required"
    else:
        idempotency = "none"
    return OperationDescriptor(
        scope=policy["authz_action"],
        stability=row["stability"],
        idempotency=idempotency,
        replay_class=row["replay_class"],
        audited=bool(policy["audited"]),
        mutates=mutates,
    )


async def fetch_engine_contract(
    client: Any,
) -> tuple[dict[str, OperationDescriptor], list[ScopeRegistryEntry]]:
    """Every engine method descriptor and the scope registry, following pages.

    Raises ``EngineCapabilityUnavailable`` naming ``DescribeContract`` while the
    pinned engine client lacks the read.
    """
    require_engine_capability("DescribeContract")
    methods: dict[str, OperationDescriptor] = {}
    scopes: list[ScopeRegistryEntry] = []
    cursor: str | None = None
    digest: str | None = None
    for _ in range(_MAX_PAGES):
        page = await client.describe_contract(cursor=cursor, limit=500)
        if digest is not None and page["schema_digest"] != digest:
            methods, scopes, cursor, digest = {}, [], None, None  # contract moved
            continue
        digest = page["schema_digest"]
        for row in page["methods"]:
            methods[row["id"]] = descriptor_from_engine_row(row)
        scopes.extend(ScopeRegistryEntry.model_validate(s) for s in page["scopes"])
        cursor = page.get("next_cursor")
        if cursor is None:
            return methods, scopes
    raise RuntimeError(f"the engine contract did not finish within {_MAX_PAGES} pages")
