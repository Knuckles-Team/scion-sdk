"""The approved harness manifest (SCION-SOURCE-INGEST-R013.1).

Owners approve the complete list of harnesses whose sessions are synced; the
SDK never discovers harnesses on its own. Every refusal is raised at load,
before any source call.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scion_sdk.config import setting
from scion_sdk.paths._platform import platform_config_file

__all__ = [
    "HARNESS_MANIFEST_FILE",
    "HARNESS_MANIFEST_VERSION",
    "HarnessEntry",
    "HarnessManifest",
    "HarnessManifestRefusal",
    "load_harness_manifest",
]

HARNESS_MANIFEST_FILE = "harness-manifest.json"
HARNESS_MANIFEST_VERSION = 1
REFUSAL_CODES = frozenset(
    {
        "HARNESS_MANIFEST_DUPLICATE_ENTRY",
        "HARNESS_MANIFEST_VERSION_UNKNOWN",
        "HARNESS_OWNER_MISSING",
        "HARNESS_OPERATION_UNBOUND",
    }
)


class HarnessManifestRefusal(ValueError):
    """A manifest refused at load with one named code."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown harness manifest refusal code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HarnessEntry(_Strict):
    """One approved harness and the operation that lists its sessions."""

    harness_id: str = Field(min_length=1)
    owner_repository: str
    operation: str
    revision: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    identity_schema: str = Field(min_length=1)
    cursor: Literal["opaque", "timestamp", "sequence"]


class HarnessManifest(_Strict):
    """The complete approved list; ``revision`` names this approval."""

    schema_version: int
    revision: str = Field(min_length=1)
    entries: tuple[HarnessEntry, ...]

    def manifest_digest(self) -> str:
        """SHA-256 of the canonical JSON: sorted keys, no spaces."""
        body = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(body.encode()).hexdigest()

    @classmethod
    def from_mapping(
        cls, raw: Any, *, operations: frozenset[str] | None = None
    ) -> HarnessManifest:
        """Validate ``raw``; ``operations`` is the set of bound operation names."""
        if not isinstance(raw, dict):
            raise HarnessManifestRefusal("HARNESS_MANIFEST_VERSION_UNKNOWN", "shape")
        if raw.get("schema_version") != HARNESS_MANIFEST_VERSION:
            raise HarnessManifestRefusal(
                "HARNESS_MANIFEST_VERSION_UNKNOWN", str(raw.get("schema_version"))
            )
        for entry in raw.get("entries") or ():
            if isinstance(entry, dict) and not entry.get("owner_repository"):
                raise HarnessManifestRefusal(
                    "HARNESS_OWNER_MISSING", str(entry.get("harness_id"))
                )
        try:
            manifest = cls.model_validate(raw, strict=False)
        except ValidationError as exc:
            raise HarnessManifestRefusal(
                "HARNESS_MANIFEST_VERSION_UNKNOWN", "invalid entry"
            ) from exc
        dupes = [
            key
            for key, n in Counter(e.harness_id for e in manifest.entries).items()
            if n > 1
        ]
        if dupes:
            raise HarnessManifestRefusal("HARNESS_MANIFEST_DUPLICATE_ENTRY", dupes[0])
        if operations is not None:
            for entry in manifest.entries:
                if entry.operation not in operations:
                    raise HarnessManifestRefusal(
                        "HARNESS_OPERATION_UNBOUND", entry.harness_id
                    )
        return manifest


def load_harness_manifest(
    path: Path | None = None, *, operations: frozenset[str] | None = None
) -> HarnessManifest:
    """Load the manifest from ``path`` or ``~/.config/arbora/harness-manifest.json``."""
    configured = setting("HARNESS_MANIFEST_PATH", "")
    target = (
        path
        or (Path(configured) if configured else None)
        or platform_config_file(HARNESS_MANIFEST_FILE)
    )
    return HarnessManifest.from_mapping(
        json.loads(target.read_text(encoding="utf-8")), operations=operations
    )
