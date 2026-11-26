"""Harness-session sync covers every approved entry (SCION-SOURCE-INGEST-R013.2-R013.5)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from scion_sdk.adapters.harness_sessions import (
    HARNESS_REFUSAL_CODES,
    EngineHarnessIndex,
    HarnessPage,
    HarnessRefusal,
    HarnessSessionSourceAdapter,
    HarnessSourceDenied,
    SessionReceipt,
    session_key,
    stream_name,
)
from scion_sdk.engine_capability import (
    EngineCapabilityUnavailable,
    engine_capability_present,
)
from scion_sdk.manifest.harness import HarnessEntry, HarnessManifest


def _entry(name: str) -> dict[str, Any]:
    return {
        "harness_id": name,
        "owner_repository": "owner/repo",
        "operation": f"list_{name}",
        "revision": "r1",
        "authority": "owner",
        "identity_schema": "session/v1",
        "cursor": "opaque",
    }


MANIFEST = HarnessManifest.from_mapping(
    {
        "schema_version": 1,
        "revision": "m1",
        "entries": [_entry("alpha"), _entry("beta"), _entry("gamma")],
    }
)


def _session(n: int, harness: str = "x", **over: Any) -> dict[str, Any]:
    return {
        "source_session_id": f"{harness}-{n}",
        "source_revision": "v1",
        "body": "private content",
    } | over


class _Source:
    """Pages of sessions with a counted call log."""

    def __init__(self, sessions: list[dict[str, Any]], *, size: int = 2) -> None:
        self.sessions, self.size, self.calls = sessions, size, []

    async def __call__(
        self, entry: HarnessEntry, cursor: str | None, limit: int
    ) -> HarnessPage:
        self.calls.append(cursor)
        start = int(cursor or 0)
        chunk = self.sessions[start : start + self.size]
        end = start + len(chunk)
        done = end >= len(self.sessions)
        return HarnessPage(tuple(chunk), None if done else str(end), done)


class _Denied:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, entry: HarnessEntry, cursor: str | None, limit: int):
        self.calls += 1
        raise HarnessSourceDenied


class _Ingest:
    """A durable fake engine: accepted cursor per stream, deduplicated by key."""

    def __init__(self) -> None:
        self.cursors: dict[str, str | None] = {}
        self.keys: dict[str, SessionReceipt] = {}
        self.stored: list[Sequence[Mapping[str, Any]]] = []

    async def accepted_cursor(self, stream: str) -> str | None:
        return self.cursors.get(stream)

    async def submit(self, stream, *, idempotency_key, sessions, cursor):
        if idempotency_key in self.keys:
            return self.keys[idempotency_key]
        receipt = SessionReceipt(f"r-{len(self.keys) + 1}", f"d-{len(self.keys) + 1}")
        self.keys[idempotency_key] = receipt
        self.cursors[stream] = cursor
        self.stored.append(sessions)
        return receipt


class _Index:
    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []

    async def index(self, batch: Mapping[str, Any]) -> Mapping[str, Any]:
        self.batches.append(dict(batch))
        return {"indexed": len(batch["records"])}


def _adapter(ops, ingest=None, index=None, tenant="t1"):
    ingest, index = ingest or _Ingest(), index or _Index()
    return (
        HarnessSessionSourceAdapter(
            MANIFEST,
            ops,
            ingest,
            index,
            tenant=tenant,
            page_sessions=5,
        ),
        ingest,
        index,
    )


def _ops(alpha=None, beta=None, gamma=None):
    return {
        "list_alpha": alpha or _Source([_session(n, "alpha") for n in range(5)]),
        "list_beta": beta or _Source([_session(n, "beta") for n in range(3)]),
        "list_gamma": gamma or _Source([_session(0, "gamma")]),
    }


@pytest.mark.spec("SCION-SOURCE-INGEST-R013.2")
async def test_full_coverage_dedup_restart_and_per_source_progress() -> None:
    ops = _ops()
    adapter, ingest, index = _adapter(ops)
    report = await adapter.sync()
    by = report.by_harness()
    assert [o.sessions for o in report.outcomes] == [5, 3, 1]
    assert {o.outcome for o in report.outcomes} == {"complete"}
    assert set(ingest.cursors) == {stream_name(h) for h in ("alpha", "beta", "gamma")}
    first_receipts = len(ingest.keys)
    # a rerun resumes from the accepted cursor and deduplicates identical pages
    again, _, _ = _adapter(_ops(), ingest, _Index())
    await again.sync()
    assert len(ingest.keys) == first_receipts
    # only a new revision of one session is a new key
    key = session_key("t1", "alpha", "alpha-0", "v1")
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert key != session_key("t1", "alpha", "alpha-0", "v2")
    assert key != session_key("t2", "alpha", "alpha-0", "v1")
    assert by["beta"].stream == "harness-sessions/beta"
    assert index.batches  # one index call per accepted page


@pytest.mark.spec("SCION-SOURCE-INGEST-R013.4")
async def test_refusals_make_zero_denied_calls_and_keep_cursors() -> None:
    assert {
        "HARNESS_OPERATION_UNKNOWN",
        "HARNESS_IDENTITY_INVALID",
        "HARNESS_CURSOR_STALE",
        "HARNESS_TENANT_SCOPE_MISSING",
        "HARNESS_SOURCE_DENIED",
    } <= HARNESS_REFUSAL_CODES
    denied = _Denied()
    adapter, ingest, _ = _adapter(_ops(beta=denied))
    by = (await adapter.sync()).by_harness()
    assert by["beta"].refusal == "HARNESS_SOURCE_DENIED"
    assert by["beta"].outcome == "partial" and by["beta"].coverage == "unavailable"
    assert denied.calls == 1  # no retry against a denying source
    assert stream_name("beta") not in ingest.cursors
    assert by["alpha"].outcome == "complete" and by["gamma"].outcome == "complete"
    # refusals before any effect
    ops = _ops()
    del ops["list_gamma"]
    clean, ingest2, _ = _adapter(ops)
    with pytest.raises(HarnessRefusal) as unknown:
        await clean.sync()
    assert unknown.value.code == "HARNESS_OPERATION_UNKNOWN" and not ingest2.keys
    notenant, ingest3, _ = _adapter(_ops(), tenant="")
    with pytest.raises(HarnessRefusal) as scope:
        await notenant.sync()
    assert scope.value.code == "HARNESS_TENANT_SCOPE_MISSING" and not ingest3.keys
    bad = _Source([_session(0, "alpha"), {"source_session_id": "x"}], size=2)
    foreign = _Source([_session(0, "beta", tenant="other")])
    stale_page = SimpleNamespace(calls=0)

    async def stuck(entry, cursor, limit):
        stale_page.calls += 1
        return HarnessPage((_session(stale_page.calls, "gamma"),), "same", False)

    adapter, ingest4, _ = _adapter(_ops(alpha=bad, beta=foreign, gamma=stuck))
    got = (await adapter.sync()).by_harness()
    assert got["alpha"].refusal == "HARNESS_IDENTITY_INVALID"
    assert got["beta"].refusal == "HARNESS_TENANT_SCOPE_MISSING"
    assert stream_name("beta") not in ingest4.cursors and not any(
        s.get("tenant") == "other" for batch in ingest4.stored for s in batch
    )
    assert got["gamma"].refusal == "HARNESS_CURSOR_STALE"
    with pytest.raises(ValueError):
        HarnessRefusal("NOT_A_CODE")


@pytest.mark.spec("SCION-SOURCE-INGEST-R013.5")
async def test_receipts_survive_restart_and_cover_every_entry() -> None:
    adapter, ingest, _ = _adapter(_ops(beta=_Denied()))
    report = await adapter.sync()
    assert {o.harness_id for o in report.outcomes} == {"alpha", "beta", "gamma"}
    digest = MANIFEST.manifest_digest()
    for outcome in report.outcomes:
        assert outcome.manifest_version == 1 and outcome.manifest_digest == digest
        assert outcome.tenant == "t1" and outcome.revision == "r1"
        assert outcome.coverage in {"declared", "received", "indexed", "unavailable"}
        assert "private content" not in repr(outcome)
    by = report.by_harness()
    assert by["alpha"].coverage == "indexed" and by["alpha"].receipt_id
    assert by["beta"].coverage == "unavailable" and by["beta"].receipt_id is None
    # restart: a new adapter over the same durable ingest reports the same receipts
    again, _, _ = _adapter(_ops(beta=_Denied()), ingest, _Index())
    assert (await again.sync()).by_harness()["alpha"].receipt_id == by[
        "alpha"
    ].receipt_id


@pytest.mark.spec("SCION-SOURCE-INGEST-R013.3")
async def test_adapter_calls_operation_then_index_once_per_page() -> None:
    source = _Source([_session(n, "alpha") for n in range(5)], size=2)
    adapter, _ingest, index = _adapter(_ops(alpha=source))
    await adapter.sync()
    pages = [b for b in index.batches if b["manifest_id"] == "alpha"]
    assert len(pages) == 3  # 2 + 2 + 1 sessions
    first = pages[0]
    assert first["tenant_id"] == "t1" and first["manifest_revision"] == "m1"
    assert first["manifest_digest"] == MANIFEST.manifest_digest()
    assert first["sync_receipt_id"] == "r-1" and first["source_cursor"] == "2"
    assert all("body" not in record for record in first["records"])
    assert source.calls == [None, "2", "4"]


@pytest.mark.spec("SCION-SOURCE-INGEST-R013.3")
async def test_index_refuses_naming_the_method_while_the_pin_lacks_it() -> None:
    if engine_capability_present("IndexHarnessSessionBatch"):
        pytest.skip("the pin serves IndexHarnessSessionBatch")
    with pytest.raises(EngineCapabilityUnavailable) as caught:
        await EngineHarnessIndex(object()).index({"records": []})
    assert caught.value.method == "IndexHarnessSessionBatch"


async def test_engine_index_calls_the_client_once_the_pin_serves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real path against a structural double of the engine client."""
    import scion_sdk.adapters.harness_sessions as module

    monkeypatch.setattr(module, "require_engine_capability", lambda _method: None)
    sent: list[dict[str, Any]] = []

    async def index_harness_session_batch(batch: dict[str, Any]) -> dict[str, Any]:
        sent.append(batch)
        return {"indexed": 1, "unchanged": 0, "unavailable": 0, "index_revision": "i1"}

    client = SimpleNamespace(
        graph=SimpleNamespace(index_harness_session_batch=index_harness_session_batch)
    )
    result = await EngineHarnessIndex(client).index({"tenant_id": "t1", "records": []})
    assert result["indexed"] == 1 and sent == [{"tenant_id": "t1", "records": []}]
