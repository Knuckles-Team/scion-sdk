"""The approved harness manifest refuses bad lists at load (SCION-SOURCE-INGEST-R013.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scion_sdk.manifest.harness import (
    HARNESS_MANIFEST_FILE,
    HARNESS_MANIFEST_VERSION,
    HarnessManifest,
    HarnessManifestRefusal,
    load_harness_manifest,
)


def _entry(harness_id: str = "alpha", **over: Any) -> dict[str, Any]:
    return {
        "harness_id": harness_id,
        "owner_repository": "owner/repo",
        "operation": "list_sessions",
        "revision": "r1",
        "authority": "owner",
        "identity_schema": "session/v1",
        "cursor": "opaque",
    } | over


def _manifest(*entries: dict[str, Any], version: int = 1) -> dict[str, Any]:
    return {"schema_version": version, "revision": "m1", "entries": list(entries)}


@pytest.mark.spec("SCION-SOURCE-INGEST-R013.1")
def test_manifest_rejects_duplicates_unknown_versions_missing_owners_and_unbound_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops = frozenset({"list_sessions"})
    ok = HarnessManifest.from_mapping(
        _manifest(_entry("a"), _entry("b")), operations=ops
    )
    assert re.fullmatch(r"[0-9a-f]{64}", ok.manifest_digest())
    assert (
        ok.manifest_digest()
        == HarnessManifest.from_mapping(
            _manifest(_entry("a"), _entry("b"))
        ).manifest_digest()
    )
    cases = {
        "HARNESS_MANIFEST_DUPLICATE_ENTRY": _manifest(_entry("a"), _entry("a")),
        "HARNESS_MANIFEST_VERSION_UNKNOWN": _manifest(_entry("a"), version=9),
        "HARNESS_OWNER_MISSING": _manifest(_entry("a", owner_repository="")),
        "HARNESS_OPERATION_UNBOUND": _manifest(_entry("a", operation="nope")),
    }
    for code, raw in cases.items():
        with pytest.raises(HarnessManifestRefusal) as caught:
            HarnessManifest.from_mapping(raw, operations=ops)
        assert caught.value.code == code
    with pytest.raises(ValueError):
        HarnessManifestRefusal("NOT_A_CODE")
    assert HARNESS_MANIFEST_VERSION == 1
    path = tmp_path / HARNESS_MANIFEST_FILE
    path.write_text(json.dumps(_manifest(_entry("a"))))
    assert load_harness_manifest(path).entries[0].harness_id == "a"
    monkeypatch.setenv("HARNESS_MANIFEST_PATH", str(path))
    assert load_harness_manifest(operations=ops).revision == "m1"
