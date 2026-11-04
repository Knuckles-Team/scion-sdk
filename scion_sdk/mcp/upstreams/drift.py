"""Schema-fingerprint drift detection per upstream (R914.11).

After each fresh ``tools/list`` the registry fingerprints the upstream as the
digest of the sorted pairs of tool name and
:func:`compatibility_fingerprint`. A declaration may pin an
``expected_fingerprint``; a mismatch is drift. Drift reuses
:func:`compatibility_fingerprint` and adds no second normalizer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from scion_sdk.manifest.tool_schema import compatibility_fingerprint

__all__ = [
    "DriftReport",
    "diff_tool_fingerprints",
    "fingerprint_tools",
    "read_tool_schema",
    "tool_fingerprint_map",
]

_MAX_DRIFT_NAMES = 50


def read_tool_schema(tool: Any) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """``(name, input_schema, output_schema)`` of a listed tool, tolerantly."""
    name = str(getattr(tool, "name", "") or "")
    input_schema = getattr(tool, "parameters", None)
    if not isinstance(input_schema, dict):
        input_schema = getattr(tool, "input_schema", None)
    if not isinstance(input_schema, dict):
        input_schema = {}
    output_schema = getattr(tool, "output_schema", None)
    if not isinstance(output_schema, dict):
        output_schema = None
    return name, input_schema, output_schema


def tool_fingerprint_map(tools: Sequence[Any]) -> dict[str, str]:
    """``name -> compatibility_fingerprint`` for every listed tool."""
    mapping: dict[str, str] = {}
    for tool in tools:
        name, input_schema, output_schema = read_tool_schema(tool)
        if name:
            mapping[name] = compatibility_fingerprint(name, input_schema, output_schema)
    return mapping


def fingerprint_tools(tools: Sequence[Any]) -> str:
    """The digest of the sorted ``(name, compatibility_fingerprint)`` pairs."""
    pairs = sorted(tool_fingerprint_map(tools).items())
    payload = json.dumps(
        pairs, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DriftReport:
    """What changed between two fingerprints, bounded to 50 names per bucket."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()

    @property
    def is_drifted(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def names(self) -> tuple[str, ...]:
        """Every changed name, capped at :data:`_MAX_DRIFT_NAMES`."""
        return (self.added + self.removed + self.changed)[:_MAX_DRIFT_NAMES]


def diff_tool_fingerprints(
    previous: dict[str, str] | None, current: dict[str, str]
) -> DriftReport:
    """The added/removed/changed tool names between two name-to-fingerprint maps."""
    previous_map = previous or {}
    added = tuple(sorted(set(current) - set(previous_map)))
    removed = tuple(sorted(set(previous_map) - set(current)))
    changed = tuple(
        sorted(
            name
            for name in set(current) & set(previous_map)
            if current[name] != previous_map[name]
        )
    )
    return DriftReport(
        added=added[:_MAX_DRIFT_NAMES],
        removed=removed[:_MAX_DRIFT_NAMES],
        changed=changed[:_MAX_DRIFT_NAMES],
    )
