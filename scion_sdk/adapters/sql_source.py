"""Approved SQL source client: checked, bounded, read-only streaming.

SCION-SOURCE-INGEST-R004.7 to R004.11. The engine approves a query and later
verifies that approval at the start, before each chunk and before the terminal
page (MEDULLA-FEDERATED-QUERY-R085). The SDK holds no lease logic: it asks the
verifier, binds the approved parameters through the driver, never into SQL
text, reads inside a read-only transaction and publishes bounded pages. Only a
terminal ``complete`` page establishes success.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, Protocol

import anyio
from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.engine_capability import require_engine_capability

__all__ = [
    "REFUSAL_CODES",
    "SqlCursor",
    "SqlExecutor",
    "SqlQueryContinuation",
    "SqlQueryPage",
    "SqlQuerySourceClient",
    "UnsupportedDialect",
    "engine_verifier",
    "register_sql_source_tools",
]

REFUSAL_CODES = frozenset(
    {
        "unsupported_dialect",
        "invalid_query",
        "unauthorized",
        "approval_expired",
        "approval_revoked",
        "stale_source",
        "query_failed",
        "limit_exceeded",
        "cancelled",
        "verification_unavailable",
    }
)
Terminal = Literal["complete", "failed", "cancelled"]
Verifier = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]


class UnsupportedDialect(Exception):
    """The driver cannot run the statement in a read-only transaction."""


class SqlCursor(Protocol):
    """An open read-only result."""

    columns: Sequence[str]

    async def fetchmany(self, size: int) -> list[tuple[Any, ...]]: ...

    async def close(self) -> None: ...


class SqlExecutor(Protocol):
    """Implemented by the connector that owns the driver."""

    async def open(self, query: str, params: Sequence[Any]) -> SqlCursor:
        """Open ``query`` read-only with native ``params``; raise
        :class:`UnsupportedDialect` when a read-only transaction is impossible."""


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SqlQueryContinuation(_Frozen):
    """Binds a continuation to one execution and one caller."""

    execution_id: str
    query_digest: str
    source: str
    schema_digest: str
    snapshot: str
    tenant: str
    owner: str
    actor: str


class SqlQueryPage(_Frozen):
    """One bounded page, with its evidence; no SQL literal or parameter value."""

    query_id: str
    execution_id: str
    sequence: int = Field(ge=0)
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    cumulative_rows: int = Field(ge=0)
    cumulative_bytes: int = Field(ge=0)
    terminal: Terminal | None = None
    reason: str | None = None
    continuation: SqlQueryContinuation | None = None
    approval_id: str = ""
    query_digest: str = ""
    verification: dict[str, Any] | None = None
    queue_high_water: int = 0
    peak_resident_bytes: int = 0


def engine_verifier(client: Any, source_approval: Mapping[str, Any]) -> Verifier:
    """Verify through the engine; refuses while the pinned client lacks it."""
    require_engine_capability("VerifySourceQueryApproval")

    async def verify(request: dict[str, Any]) -> Mapping[str, Any]:
        verdict: Mapping[str, Any] = await client.verify_source_query_approval(
            request, source_approval
        )
        return verdict

    return verify


def _refusal_for(error: Exception) -> str:
    code = str(getattr(error, "code", "")).upper()
    for word, reason in (
        ("EXPIRED", "approval_expired"),
        ("REVOKED", "approval_revoked"),
        ("STALE", "stale_source"),
        ("CHANGED", "stale_source"),
        ("CALLER", "unauthorized"),
        ("UNAUTHORIZED", "unauthorized"),
    ):
        if word in code:
            return reason
    return "verification_unavailable"


def _size(row: tuple[Any, ...]) -> int:
    return len(json.dumps(row, default=str))


class _Run:
    def __init__(
        self,
        client: SqlQuerySourceClient,
        approved: dict[str, Any],
        observed: Mapping[str, str],
    ) -> None:
        self.client, self.approved, self.observed = client, approved, observed
        self.execution_id = uuid.uuid4().hex
        self.limits: dict[str, int] = approved["request"]["limits"]
        self.sequence = self.rows = self.bytes = self.high_water = 0
        self.cancelled = anyio.Event()
        self.receipt: dict[str, Any] | None = None

    async def verify(self, phase: str) -> None:
        self.receipt = dict(
            await self.client.verifier(
                {
                    "approved_query": self.approved,
                    "observed_source_revision": self.observed["source_revision"],
                    "observed_schema_digest": self.observed["schema_digest"],
                    "observed_snapshot_ref": self.observed["snapshot_ref"],
                    "phase": phase,
                }
            )
        )

    def continuation(self) -> SqlQueryContinuation:
        a = self.approved
        return SqlQueryContinuation(
            execution_id=self.execution_id,
            query_digest=a["query_digest"],
            source=a["request"]["source_ref"],
            schema_digest=a["request"]["schema_digest"],
            snapshot=a["request"]["snapshot_ref"],
            tenant=a["tenant_ref"],
            owner=a["owner_ref"],
            actor=a["actor_ref"],
        )

    def page(
        self,
        columns: Sequence[str],
        rows: list[tuple[Any, ...]],
        size: int,
        terminal: Terminal | None = None,
        reason: str | None = None,
    ) -> SqlQueryPage:
        self.rows += len(rows)
        self.bytes += size
        page = SqlQueryPage(
            query_id=self.approved["query_id"],
            execution_id=self.execution_id,
            sequence=self.sequence,
            columns=tuple(columns),
            rows=tuple(rows),
            row_count=len(rows),
            byte_count=size,
            cumulative_rows=self.rows,
            cumulative_bytes=self.bytes,
            terminal=terminal,
            reason=reason,
            continuation=None if terminal else self.continuation(),
            approval_id=self.approved["approval_id"],
            query_digest=self.approved["query_digest"],
            verification=self.receipt,
            queue_high_water=self.high_water,
            peak_resident_bytes=max(size, 0),
        )
        self.sequence += 1
        return page

    def end(self, columns: Sequence[str], reason: str) -> SqlQueryPage:
        terminal: Terminal = "cancelled" if reason == "cancelled" else "failed"
        return self.page(columns, [], 0, terminal, reason)


class SqlQuerySourceClient:
    """Runs one approved, engine-verified, read-only query as bounded pages."""

    def __init__(self, executor: SqlExecutor, verifier: Verifier) -> None:
        self.executor = executor
        self.verifier = verifier
        self._live: dict[str, _Run] = {}

    def cancel_approved_query(self, execution_id: str) -> bool:
        """Cancel a local execution handle; it grants no authority."""
        run = self._live.get(execution_id)
        if run is not None:
            run.cancelled.set()
        return run is not None

    async def execute_approved_query(
        self, approved: dict[str, Any], observed: Mapping[str, str]
    ) -> AsyncIterator[SqlQueryPage]:
        """Yield bounded pages ending in exactly one terminal page."""
        run = _Run(self, approved, observed)
        self._live[run.execution_id] = run
        cursor: SqlCursor | None = None
        columns: Sequence[str] = ()
        try:
            try:
                await self._guard(run, "start")
                params = [
                    _value(p["value"])
                    for p in sorted(
                        approved["request"]["parameters"], key=lambda p: p["position"]
                    )
                ]
                cursor = await self.executor.open(approved["request"]["sql"], params)
                columns = cursor.columns
                async for page in self._stream(run, cursor, columns):
                    yield page
            except UnsupportedDialect:
                yield run.end(columns, "unsupported_dialect")
            except _Stop as stop:
                yield run.end(columns, stop.reason)
            except Exception:
                yield run.end(columns, "query_failed")
        finally:
            self._live.pop(run.execution_id, None)
            if cursor is not None:
                with anyio.CancelScope(shield=True):
                    await cursor.close()

    async def _stream(
        self, run: _Run, cursor: SqlCursor, columns: Sequence[str]
    ) -> AsyncIterator[SqlQueryPage]:
        """Pull one chunk at a time: the consumer's pace is the backpressure."""
        limits = run.limits
        rows = nbytes = 0
        deadline = anyio.current_time() + limits["deadline_ms"] / 1000
        while True:
            try:
                with anyio.fail_after(max(deadline - anyio.current_time(), 0)):
                    chunk = await cursor.fetchmany(limits["chunk_rows"])
            except TimeoutError:
                raise _Stop("limit_exceeded") from None
            if not chunk:
                break
            size = sum(_size(r) for r in chunk)
            rows, nbytes = rows + len(chunk), nbytes + size
            if (
                rows > limits["max_rows"]
                or nbytes > limits["max_bytes"]
                or size > limits["chunk_bytes"]
            ):
                raise _Stop("limit_exceeded")
            run.high_water = 1
            await self._guard(run, "chunk")
            yield run.page(columns, chunk, size)
        await self._guard(run, "complete")
        yield run.page(columns, [], 0, "complete")

    async def _guard(self, run: _Run, phase: str) -> None:
        if run.cancelled.is_set():
            raise _Stop("cancelled")
        try:
            await run.verify(phase)
        except Exception as error:
            raise _Stop(_refusal_for(error)) from None


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _value(value: Mapping[str, Any]) -> Any:
    return None if value["kind"] == "null" else value["value"]


def register_sql_source_tools(
    server: Any, client: SqlQuerySourceClient, observe: Callable[[], Mapping[str, str]]
) -> None:
    """Register the receiver and cancel operations on a connector server."""

    async def execute_approved_query(approved: dict[str, Any]) -> list[dict[str, Any]]:
        """Run one engine-approved query; returns its bounded pages."""
        return [
            page.model_dump(mode="json")
            async for page in client.execute_approved_query(approved, observe())
        ]

    async def cancel_approved_query(execution_id: str) -> bool:
        """Cancel a running execution."""
        return client.cancel_approved_query(execution_id)

    server.tool(name="execute_approved_query")(execute_approved_query)
    server.tool(name="cancel_approved_query")(cancel_approved_query)
