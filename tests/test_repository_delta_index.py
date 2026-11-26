"""Delta repository indexing reads changed files only (SCION-SOURCE-INGEST-R004.14-R004.17)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from scion_sdk.adapters.mcp_tool import INDEX_STATE_KEY, McpToolSourceAdapter
from scion_sdk.manifest.model import SchemaMapping
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.ports.source_adapter import IndexStateAdapter
from scion_sdk.repository import (
    DeltaIndexConfig,
    DeltaIndexSource,
    IndexPage,
    IndexRefusal,
    RepositoryIndexState,
    accept_index_page,
    index_delta,
)
from scion_sdk.repository.delta import LANGUAGE_MATRIX, FileResolution
from scion_sdk.repository.errors import INDEX_REFUSAL_CODES
from scion_sdk.repository.indexing import RepositoryBatchReceipt
from scion_sdk.repository.models import RepositoryFile
from scion_sdk.runner.syncing import SyncTarget, sync_stream
from scion_sdk.testing.sinks import InMemorySink
from scion_sdk.transports.mcp import McpTransport

CLOCK = datetime(2026, 11, 26, tzinfo=UTC)


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


class _Source:
    """A repository whose reads and releases are counted."""

    def __init__(self, files: Mapping[str, bytes], *, leak: bool = False) -> None:
        self.files = dict(files)
        self.reads: list[str] = []
        self.held: set[str] = set()
        self.leak = leak

    def digests(self) -> Mapping[str, str]:
        return {p: _digest(c) for p, c in self.files.items()}

    def read(self, path: str) -> bytes:
        self.reads.append(path)
        self.held.add(path)
        return self.files[path]

    def release(self, paths: tuple[str, ...]) -> None:
        if not self.leak:
            self.held -= set(paths)

    def unreleased(self) -> int:
        return len(self.held)


def _result(paths: tuple[str, ...], files: tuple[RepositoryFile, ...], bad: str = ""):
    outcomes = [
        SimpleNamespace(
            file_path=f.path,
            status=SimpleNamespace(value="error" if f.path == bad else "success"),
            diagnostics=[SimpleNamespace(code="PARSE")] if f.path == bad else [],
        )
        for f in files
    ]
    return SimpleNamespace(file_outcomes=outcomes)


def _submitter(batches: list[tuple[str, ...]], bad: str = ""):
    async def submit(_client: Any, files: tuple[RepositoryFile, ...]):
        paths = tuple(f.path for f in files)
        batches.append(paths)
        return RepositoryBatchReceipt(paths=paths, result=_result(paths, files, bad))  # type: ignore[arg-type]

    return submit


async def _run(source, known, previous=None, *, bad="", batches=None, **config):
    return await index_delta(
        None,  # type: ignore[arg-type]
        repository="repo-a",
        source=source,
        known_digests=known,
        previous=previous,
        config=DeltaIndexConfig(**config),
        layout_revision="v1",
        now=lambda: CLOCK,
        parser_versions={"python": "1"},
        submit=_submitter([] if batches is None else batches, bad),
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.14")
async def test_delta_reads_only_changed_files() -> None:
    source = _Source({"a.py": b"a = 1\n", "b.py": b"b = 2\n", "c.py": b"c = 3\n"})
    batches: list[tuple[str, ...]] = []
    first = await _run(source, {}, batches=batches, batch_files=2)
    assert sorted(source.reads) == ["a.py", "b.py", "c.py"]
    assert batches == [("a.py", "b.py"), ("c.py",)]
    known = source.digests()
    source.reads.clear()
    source.files["b.py"] = b"b = 22\n"
    second = await _run(source, known, first.state(), batches=batches, batch_files=2)
    assert source.reads == ["b.py"]
    assert [f.path for f in second.files] == ["b.py"]
    assert second.sequence == 2
    source.reads.clear()
    third = await _run(source, source.digests(), second.state())
    assert source.reads == [] and third.files == ()


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.16")
async def test_refusals_isolate_one_file_and_require_release() -> None:
    assert {
        "INDEX_LANGUAGE_UNSUPPORTED",
        "INDEX_TYPE_MODE_INVALID",
        "INDEX_SEQUENCE_STALE",
        "INDEX_SCHEMA_MISMATCH",
        "INDEX_RELEASE_PENDING",
    } <= INDEX_REFUSAL_CODES
    source = _Source({"ok.py": b"x\n", "bad.py": b"def (\n", "notes.txt": b"hi\n"})
    page = await _run(source, {}, bad="bad.py")
    by_path = {f.path: f for f in page.files}
    assert by_path["ok.py"].resolution == "syntax_only"
    assert by_path["bad.py"].resolution == "failed"
    assert by_path["bad.py"].reason == "PARSE"
    assert by_path["notes.txt"].reason == "INDEX_LANGUAGE_UNSUPPORTED"
    assert source.reads == ["bad.py", "ok.py"]
    with pytest.raises(IndexRefusal) as leak:
        await _run(_Source({"a.py": b"a\n"}, leak=True), {})
    assert leak.value.code == "INDEX_RELEASE_PENDING"
    with pytest.raises(IndexRefusal) as mode:
        await index_delta(
            None,  # type: ignore[arg-type]
            repository="repo-a",
            source=_Source({}),
            known_digests={},
            previous=None,
            config=DeltaIndexConfig.model_construct(
                batch_files=1, peak_bytes=1 << 30, semantic_mode="semantic"
            ),
            layout_revision="v1",
            now=lambda: CLOCK,
        )
    assert mode.value.code == "INDEX_TYPE_MODE_INVALID"
    # a stale sequence never advances: the state must strictly grow
    state = page.state()
    with pytest.raises(IndexRefusal) as stale:
        accept_index_page(page.model_dump(mode="json"), state)
    assert stale.value.code == "INDEX_SEQUENCE_STALE"
    with pytest.raises(IndexRefusal) as schema:
        accept_index_page({"repository": "x"}, None)
    assert schema.value.code == "INDEX_SCHEMA_MISMATCH"
    with pytest.raises(ValueError):
        IndexRefusal("NOT_A_CODE")


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.16")
async def test_memory_bound_refuses_before_the_engine_is_called() -> None:
    source = _Source({"big.py": b"x" * 200, "small.py": b"y\n"})
    batches: list[tuple[str, ...]] = []
    with pytest.raises(IndexRefusal) as caught:
        await index_delta(
            None,  # type: ignore[arg-type]
            repository="repo-a",
            source=source,
            known_digests={},
            previous=None,
            config=DeltaIndexConfig.model_construct(
                batch_files=10, peak_bytes=100, semantic_mode="syntax"
            ),
            layout_revision="v1",
            now=lambda: CLOCK,
            submit=_submitter(batches),
        )
    assert caught.value.code == "INDEX_MEMORY_EXCEEDED"
    assert batches == [] and source.unreleased() == 0


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.17")
async def test_page_evidence_attributes_memory_and_files() -> None:
    source = _Source({"src/a.py": b"a = 1\n", "docs/readme.txt": b"hello\n"})
    page = await _run(source, {}, semantic_mode="typed")
    dumped = page.model_dump(mode="json")
    assert dumped["repository"] == "repo-a" and dumped["sequence"] == 1
    assert dumped["changed_files_digest"].startswith("sha256:")
    assert len(dumped["changed_files_digest"]) == 71
    assert dumped["parser_versions"] == {"python": "1"}
    assert dumped["semantic_mode"] == "typed"
    assert dumped["languages"] == {"python": 1}
    assert dumped["peak_resident_bytes"] == len(b"a = 1\n")
    assert dumped["released_at"].startswith("2026-11-26")
    resolutions = {f["path"]: f["resolution"] for f in dumped["files"]}
    assert resolutions == {"src/a.py": "resolved", "docs/readme.txt": "unsupported"}
    # paths stay repository-relative: an absolute path is not an index path
    with pytest.raises(ValueError):
        RepositoryFile(path="/abs/a.py", blob_digest=_digest(b"x"), content=b"x")
    assert IndexPage.model_validate(dumped) == page
    typed_again = await _run(_Source({"src/a.py": b"a = 1\n"}), {})
    assert typed_again.files[0].resolution == "syntax_only"


def _tool_server(received: list[Any]) -> FastMCP:
    server = FastMCP("repo-fixture")

    @server.tool(name="index_repository", annotations={"readOnlyHint": True})
    async def index_repository(index_state: dict[str, Any] | None = None):
        received.append(index_state)
        sequence = 1 if index_state is None else index_state["sequence"] + 1
        page = IndexPage(
            repository="repo-a",
            sequence=sequence,
            changed_files_digest=_digest(b"changed"),
            parser_versions={"python": "1"},
            semantic_mode="syntax",
            layout_revision="v1",
            languages={"python": 1},
            peak_resident_bytes=6,
            released_at=CLOCK,
            files=(),
        )
        return {
            "files": [{"id": f"file-{sequence}"}],
            "index_page": page.model_dump(mode="json"),
        }

    return server


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.15")
async def test_state_flows_worker_to_adapter() -> None:
    preset = ToolPreset(
        name="index",
        server="repo-fixture",
        tool="index_repository",
        id_field="id",
        records_path="files",
        params_style="args",
    )
    adapter = McpToolSourceAdapter(
        preset,
        connector="repo-fixture",
        mapping_reference="connector:repo-fixture#mappings/File",
        schema_mappings={"File": SchemaMapping(ontology_class="Document")},
    )
    assert isinstance(adapter, IndexStateAdapter)
    assert adapter.index_state() is None
    received: list[Any] = []
    sink = InMemorySink()
    target = SyncTarget("repo-fixture", sink, max_pages=1)
    async with McpTransport().session(
        TransportEndpoint(in_process=_tool_server(received))
    ) as session:
        await adapter.discover(session)
        await sync_stream(session, adapter, target)
        first = adapter.index_state()
        await sync_stream(session, adapter, target)
    assert isinstance(first, RepositoryIndexState) and first.sequence == 1
    assert received[0] is None
    assert received[1]["sequence"] == 1
    state = adapter.index_state()
    assert state is not None and state.sequence == 2
    assert INDEX_STATE_KEY == "index_state"


def test_language_matrix_and_source_contract() -> None:
    source: DeltaIndexSource = _Source({"a.py": b"a\n"})
    assert set(source.digests()) == {"a.py"}
    assert LANGUAGE_MATRIX[".py"] == "python"
    item = FileResolution(path="a.py", language="python", resolution="resolved")
    assert item.reason == ""
