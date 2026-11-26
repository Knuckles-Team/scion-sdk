"""The approved SQL client: contract, verification boundaries, evidence (R004.7-.11)."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

import pytest

from scion_sdk.adapters.sql_source import (
    REFUSAL_CODES,
    SqlCursor,
    SqlExecutor,
    SqlQueryContinuation,
    SqlQueryPage,
    SqlQuerySourceClient,
    UnsupportedDialect,
    engine_verifier,
    register_sql_source_tools,
)
from scion_sdk.engine_capability import (
    EngineCapabilityUnavailable,
    engine_capability_present,
)

OBSERVED = {"source_revision": "r1", "schema_digest": "s1", "snapshot_ref": "snap"}
SECRET = "literal-secret-value"  # sanitizer:ignore - synthetic redaction probe, not a credential


def approved(**limits: int) -> dict[str, Any]:
    base = {
        "chunk_bytes": 10_000,
        "chunk_rows": 2,
        "deadline_ms": 60_000,
        "max_bytes": 100_000,
        "max_resident_bytes": 100_000,
        "max_rows": 100,
        "queue_bytes": 10_000,
        "queue_depth": 1,
    }
    return {
        "approval_id": "ap-1",
        "query_id": "q-1",
        "query_digest": "d" * 8,
        "tenant_ref": "t",
        "owner_ref": "o",
        "actor_ref": "a",
        "request": {
            "sql": "SELECT id, name FROM item WHERE id > ?",
            "source_ref": "src",
            "schema_digest": "s1",
            "snapshot_ref": "snap",
            "parameters": [{"position": 0, "value": {"kind": "int64", "value": 0}}],
            "limits": {**base, **limits},
        },
    }


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]], fail_after: int | None) -> None:
        self.columns = ("id", "name")
        self.rows, self.fail_after, self.closed, self.reads = rows, fail_after, False, 0

    async def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise RuntimeError(SECRET)
        self.reads += 1
        out, self.rows = self.rows[:size], self.rows[size:]
        return out

    async def close(self) -> None:
        self.closed = True


def _protocol_shapes() -> tuple[type, type]:
    return SqlCursor, SqlExecutor


class _Executor:
    def __init__(self, n: int = 5, fail_after: int | None = None) -> None:
        self.rows = [(i, f"n{i}") for i in range(1, n + 1)]
        self.fail_after = fail_after
        self.opened: list[tuple[str, Sequence[Any]]] = []
        self.cursor: _Cursor | None = None
        self.unsupported = False

    async def open(self, query: str, params: Sequence[Any]) -> _Cursor:
        if self.unsupported:
            raise UnsupportedDialect
        self.opened.append((query, tuple(params)))
        self.cursor = _Cursor(list(self.rows), self.fail_after)
        return self.cursor


class _Refusal(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(SECRET)
        self.code = code


class _Verifier:
    def __init__(
        self, fail_on: int | None = None, code: str = "SOURCE_QUERY_REVOKED"
    ) -> None:
        self.calls: list[str] = []
        self.fail_on, self.code = fail_on, code

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(request["phase"])
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise _Refusal(self.code)
        return {"approval_id": "ap-1", "lease_revision": len(self.calls)}


async def _run(client: SqlQuerySourceClient, **limits: int) -> list[SqlQueryPage]:
    return [
        p async for p in client.execute_approved_query(approved(**limits), OBSERVED)
    ]


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.7")
async def test_descriptor_and_page_contract() -> None:
    pages = await _run(SqlQuerySourceClient(_Executor(), _Verifier()))
    first, last = pages[0], pages[-1]
    assert first.query_id == "q-1" and first.sequence == 0 and first.row_count == 2
    assert first.columns == ("id", "name") and first.terminal is None
    assert isinstance(first.continuation, SqlQueryContinuation)
    assert (first.continuation.tenant, first.continuation.owner) == ("t", "o")
    assert first.continuation.actor == "a" and first.continuation.snapshot == "snap"
    assert last.terminal == "complete" and last.continuation is None
    assert [p.sequence for p in pages] == list(range(len(pages)))
    assert last.cumulative_rows == 5
    assert _protocol_shapes() == (SqlCursor, SqlExecutor)
    assert {"query_failed", "cancelled"} <= REFUSAL_CODES


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.8")
async def test_verification_boundaries_and_bounds() -> None:
    verifier, executor = _Verifier(), _Executor()
    pages = await _run(SqlQuerySourceClient(executor, verifier))
    assert verifier.calls == ["start"] + ["chunk"] * 3 + ["complete"]
    assert executor.opened == [("SELECT id, name FROM item WHERE id > ?", (0,))]
    assert [p.row_count for p in pages] == [2, 2, 1, 0]
    capped = await _run(SqlQuerySourceClient(_Executor(), _Verifier()), max_rows=3)
    assert capped[-1].terminal == "failed" and capped[-1].reason == "limit_exceeded"
    assert all(p.terminal != "complete" for p in capped)
    unsupported = _Executor()
    unsupported.unsupported = True
    refused = await _run(SqlQuerySourceClient(unsupported, _Verifier()))
    assert [p.reason for p in refused] == ["unsupported_dialect"]


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.8")
async def test_sqlite_read_only_native_binding() -> None:
    class Sqlite:
        async def open(self, query: str, params: Sequence[Any]) -> Any:
            db = sqlite3.connect(":memory:")
            db.execute("CREATE TABLE item (id INTEGER, name TEXT)")
            db.executemany("INSERT INTO item VALUES (?, ?)", [(1, "a"), (2, "b")])
            db.execute("PRAGMA query_only = ON")
            cursor = db.execute(query, tuple(params))

            class Wrapped:
                columns = tuple(d[0] for d in cursor.description)

                async def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
                    return cursor.fetchmany(size)

                async def close(self) -> None:
                    db.close()

            return Wrapped()

    pages = await _run(SqlQuerySourceClient(Sqlite(), _Verifier()))
    assert pages[-1].terminal == "complete" and pages[-1].cumulative_rows == 2


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.10")
async def test_revocation_and_failure_after_chunks() -> None:
    executor = _Executor(n=10)
    revoked = await _run(SqlQuerySourceClient(executor, _Verifier(fail_on=4)))
    assert revoked[-1].terminal == "failed" and revoked[-1].reason == "approval_revoked"
    assert executor.cursor is not None and executor.cursor.closed
    assert sum(1 for p in revoked if p.terminal) == 1
    broken = _Executor(n=10, fail_after=3)
    failed = await _run(SqlQuerySourceClient(broken, _Verifier()))
    assert failed[-1].reason == "query_failed" and SECRET not in repr(failed)
    assert broken.cursor is not None and broken.cursor.closed
    expired = await _run(
        SqlQuerySourceClient(
            _Executor(), _Verifier(fail_on=1, code="SOURCE_QUERY_EXPIRED")
        )
    )
    assert expired[-1].reason == "approval_expired" and len(expired) == 1
    unavailable = await _run(
        SqlQuerySourceClient(_Executor(), _Verifier(fail_on=1, code="OTHER"))
    )
    assert unavailable[-1].reason == "verification_unavailable"


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.10")
async def test_cancel_closes_the_cursor_with_one_terminal_page() -> None:
    executor, client = (
        _Executor(n=10),
        SqlQuerySourceClient(_Executor(n=10), _Verifier()),
    )
    del executor
    pages = []
    async for page in client.execute_approved_query(approved(), OBSERVED):
        pages.append(page)
        if len(pages) == 1:
            assert client.cancel_approved_query(page.execution_id)
    assert pages[-1].terminal == "cancelled" and pages[-1].reason == "cancelled"
    assert client.cancel_approved_query("absent") is False


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.11")
async def test_page_provenance_and_redaction() -> None:
    pages = await _run(SqlQuerySourceClient(_Executor(), _Verifier()))
    assert {p.approval_id for p in pages} == {"ap-1"}
    assert {p.query_digest for p in pages} == {"d" * 8}
    assert pages[0].verification is not None and pages[0].queue_high_water == 1
    assert pages[-1].cumulative_bytes == sum(p.byte_count for p in pages)
    broken = await _run(SqlQuerySourceClient(_Executor(fail_after=1), _Verifier()))
    assert SECRET not in broken[-1].model_dump_json()


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.9")
@pytest.mark.spec("SCION-SOURCE-INGEST-R004.12")
async def test_engine_verifier_is_refused_while_the_pinned_engine_lacks_it() -> None:
    if engine_capability_present("VerifySourceQueryApproval"):
        pytest.skip("the pin serves VerifySourceQueryApproval")
    with pytest.raises(EngineCapabilityUnavailable) as caught:
        engine_verifier(object(), {})
    assert caught.value.method == "VerifySourceQueryApproval"


async def test_registered_tools_run_through_a_served_connector() -> None:
    from fastmcp import Client, FastMCP

    server = FastMCP("sql")
    client = SqlQuerySourceClient(_Executor(), _Verifier())
    register_sql_source_tools(server, client, lambda: OBSERVED)
    async with Client(server) as mcp:
        result = await mcp.call_tool("execute_approved_query", {"approved": approved()})
        names = {t.name for t in await mcp.list_tools()}
    assert names == {"execute_approved_query", "cancel_approved_query"}
    assert result.data[-1]["terminal"] == "complete"
