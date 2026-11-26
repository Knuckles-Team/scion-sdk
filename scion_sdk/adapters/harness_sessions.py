"""Versioned harness-session sync (SCION-SOURCE-INGEST-R013.2 to R013.5).

The adapter visits every entry of the approved manifest, one stream per entry
named ``harness-sessions/<harness_id>``. Pages commit through the ingest port,
and after each accepted page the engine index is called once. A denied source
is a typed partial outcome: its cursor does not advance and it never hides a
healthy source.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from scion_sdk.engine_capability import require_engine_capability
from scion_sdk.manifest.harness import HarnessEntry, HarnessManifest

__all__ = [
    "HARNESS_REFUSAL_CODES",
    "EngineHarnessIndex",
    "HarnessPage",
    "HarnessRefusal",
    "HarnessSessionSourceAdapter",
    "HarnessSourceDenied",
    "SessionReceipt",
    "session_key",
    "stream_name",
]

HARNESS_REFUSAL_CODES = frozenset(
    {
        "HARNESS_OPERATION_UNKNOWN",
        "HARNESS_IDENTITY_INVALID",
        "HARNESS_CURSOR_STALE",
        "HARNESS_TENANT_SCOPE_MISSING",
        "HARNESS_SOURCE_DENIED",
    }
)
_IDENTITY = ("source_session_id", "source_revision")

Coverage = Literal["declared", "received", "indexed", "unavailable"]


class HarnessRefusal(RuntimeError):
    """A refusal with one named code; raised before any effect."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in HARNESS_REFUSAL_CODES:
            raise ValueError(f"unknown harness refusal code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class HarnessSourceDenied(RuntimeError):
    """The harness refused the caller; reported as a typed partial outcome."""


@dataclass(frozen=True)
class HarnessPage:
    """One page of sessions an entry's operation returned."""

    sessions: tuple[Mapping[str, Any], ...]
    next_cursor: str | None
    exhausted: bool


@dataclass(frozen=True)
class SessionReceipt:
    """The graph receipt of one accepted page."""

    receipt_id: str
    receipt_digest: str


class HarnessOperation(Protocol):
    """The registered operation of one entry (a connector tool)."""

    async def __call__(
        self, entry: HarnessEntry, cursor: str | None, limit: int
    ) -> HarnessPage: ...


class HarnessIngest(Protocol):
    """The ingest side: the accepted cursor and one committed page."""

    async def accepted_cursor(self, stream: str) -> str | None: ...

    async def submit(
        self,
        stream: str,
        *,
        idempotency_key: str,
        sessions: Sequence[Mapping[str, Any]],
        cursor: str | None,
    ) -> SessionReceipt: ...


class HarnessIndex(Protocol):
    """The engine index of accepted sessions."""

    async def index(self, batch: Mapping[str, Any]) -> Mapping[str, Any]: ...


class EngineHarnessIndex:
    """``IndexHarnessSessionBatch`` over the engine client, behind the pin guard."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def index(self, batch: Mapping[str, Any]) -> Mapping[str, Any]:
        """Index one batch; refuses with the method name while the pin lacks it."""
        require_engine_capability("IndexHarnessSessionBatch")
        result: Mapping[
            str, Any
        ] = await self._client.graph.index_harness_session_batch(dict(batch))
        return result


@dataclass(frozen=True)
class EntryOutcome:
    """Evidence for one entry; carries counts and ids, never session content."""

    harness_id: str
    stream: str
    manifest_version: int
    manifest_digest: str
    tenant: str
    revision: str
    cursor: str | None
    coverage: Coverage
    outcome: str
    sessions: int = 0
    receipt_id: str | None = None
    refusal: str | None = None


@dataclass(frozen=True)
class HarnessSyncReport:
    """The outcome of one sync over the complete manifest."""

    outcomes: tuple[EntryOutcome, ...] = field(default=())

    def by_harness(self) -> dict[str, EntryOutcome]:
        """Outcomes keyed by harness id."""
        return {item.harness_id: item for item in self.outcomes}


def stream_name(harness_id: str) -> str:
    """The stream one manifest entry syncs into."""
    return f"harness-sessions/{harness_id}"


def session_key(
    tenant: str, harness_id: str, source_session_id: str, source_revision: str
) -> str:
    """The idempotency key of one session revision."""
    raw = f"{tenant}|{harness_id}|{source_session_id}|{source_revision}"
    return hashlib.sha256(raw.encode()).hexdigest()


class HarnessSessionSourceAdapter:
    """Sync every approved harness; ``page_sessions`` bounds each page."""

    def __init__(
        self,
        manifest: HarnessManifest,
        operations: Mapping[str, HarnessOperation],
        ingest: HarnessIngest,
        index: HarnessIndex,
        *,
        tenant: str,
        page_sessions: int = 200,
    ) -> None:
        if not 1 <= page_sessions <= 5000:
            raise ValueError("page_sessions must be within [1, 5000]")
        self._manifest = manifest
        self._operations = operations
        self._ingest = ingest
        self._index = index
        self._tenant = tenant
        self._page = page_sessions

    def _refuse_before_effect(self) -> None:
        if not self._tenant:
            raise HarnessRefusal("HARNESS_TENANT_SCOPE_MISSING")
        for entry in self._manifest.entries:
            if entry.operation not in self._operations:
                raise HarnessRefusal("HARNESS_OPERATION_UNKNOWN", entry.harness_id)

    async def sync(self) -> HarnessSyncReport:
        """Visit every entry; one entry failing never hides another."""
        self._refuse_before_effect()
        outcomes = [await self._sync_entry(e) for e in self._manifest.entries]
        return HarnessSyncReport(tuple(outcomes))

    def _outcome(self, entry: HarnessEntry, **values: Any) -> EntryOutcome:
        base: dict[str, Any] = {
            "harness_id": entry.harness_id,
            "stream": stream_name(entry.harness_id),
            "manifest_version": self._manifest.schema_version,
            "manifest_digest": self._manifest.manifest_digest(),
            "tenant": self._tenant,
            "revision": entry.revision,
        }
        return EntryOutcome(**{**base, **values})

    async def _sync_entry(self, entry: HarnessEntry) -> EntryOutcome:
        stream = stream_name(entry.harness_id)
        cursor = await self._ingest.accepted_cursor(stream)
        total = 0
        receipt: SessionReceipt | None = None
        coverage: Coverage = "declared"
        while True:
            try:
                page = await self._operations[entry.operation](
                    entry, cursor, self._page
                )
            except HarnessSourceDenied:
                return self._outcome(
                    entry,
                    cursor=cursor,
                    coverage="unavailable",
                    outcome="partial",
                    sessions=total,
                    refusal="HARNESS_SOURCE_DENIED",
                )
            refusal = self._page_refusal(page, cursor)
            if refusal:
                return self._outcome(
                    entry,
                    cursor=cursor,
                    coverage="unavailable",
                    outcome="partial",
                    sessions=total,
                    refusal=refusal,
                )
            if page.sessions:
                receipt = await self._ingest.submit(
                    stream,
                    idempotency_key=self._page_key(entry, page),
                    sessions=page.sessions,
                    cursor=page.next_cursor,
                )
                await self._index.index(self._batch(entry, page, receipt))
                coverage = "indexed"
                total += len(page.sessions)
            cursor = page.next_cursor
            if page.exhausted:
                return self._outcome(
                    entry,
                    cursor=cursor,
                    coverage=coverage,
                    outcome="complete",
                    sessions=total,
                    receipt_id=receipt.receipt_id if receipt else None,
                )

    def _page_refusal(self, page: HarnessPage, cursor: str | None) -> str | None:
        if len(page.sessions) > self._page:
            return "HARNESS_IDENTITY_INVALID"
        for item in page.sessions:
            if item.get("tenant", self._tenant) != self._tenant:
                return "HARNESS_TENANT_SCOPE_MISSING"
            if not all(isinstance(item.get(k), str) and item[k] for k in _IDENTITY):
                return "HARNESS_IDENTITY_INVALID"
        if not page.exhausted and (
            page.next_cursor is None or page.next_cursor == cursor
        ):
            return "HARNESS_CURSOR_STALE"
        return None

    def _page_key(self, entry: HarnessEntry, page: HarnessPage) -> str:
        keys = sorted(
            session_key(
                self._tenant,
                entry.harness_id,
                str(item["source_session_id"]),
                str(item["source_revision"]),
            )
            for item in page.sessions
        )
        return hashlib.sha256("\n".join(keys).encode()).hexdigest()

    def _batch(
        self, entry: HarnessEntry, page: HarnessPage, receipt: SessionReceipt
    ) -> dict[str, Any]:
        return {
            "tenant_id": self._tenant,
            "manifest_id": entry.harness_id,
            "manifest_revision": self._manifest.revision,
            "manifest_digest": self._manifest.manifest_digest(),
            "declared_total": len(page.sessions),
            "sync_receipt_id": receipt.receipt_id,
            "sync_receipt_digest": receipt.receipt_digest,
            "source_cursor": page.next_cursor or "",
            "records": [
                {k: item[k] for k in _IDENTITY} | {"harness_id": entry.harness_id}
                for item in page.sessions
            ],
            "unavailable": [],
        }
