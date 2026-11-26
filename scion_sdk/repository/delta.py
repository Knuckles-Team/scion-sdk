"""Delta repository indexing: changed files only, bounded memory.

Implements SCION-SOURCE-INGEST-R004.14, .16 and .17. The accepted checkpoint holds
the digest of every indexed file. A run reads a file only when its digest changed,
sends the changed set to the engine in bounded batches, and ends with one evidence
page. The caller supplies the clock; nothing here reads wall time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal, Protocol

from medulla.client import MedullaClient
from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.repository.errors import IndexRefusal
from scion_sdk.repository.indexing import (
    RepositoryBatchReceipt,
    RepositoryIndexState,
    submit_repository_batch,
)
from scion_sdk.repository.models import RepositoryFile

__all__ = [
    "LANGUAGE_MATRIX",
    "DeltaIndexConfig",
    "DeltaIndexSource",
    "FileResolution",
    "IndexPage",
    "accept_index_page",
    "index_delta",
]

#: The declared language matrix: file suffix to language name.
LANGUAGE_MATRIX: Mapping[str, str] = {
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".js": "javascript",
    ".go": "go",
    ".java": "java",
}

_SHA = r"^sha256:[0-9a-f]{64}$"


class DeltaIndexConfig(BaseModel):
    """Bounds of one delta run (descriptor ``repository`` block)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_files: int = Field(default=200, ge=1, le=5000)
    peak_bytes: int = Field(default=1_073_741_824, ge=67_108_864, le=17_179_869_184)
    semantic_mode: Literal["syntax", "typed"] = "syntax"


class DeltaIndexSource(Protocol):
    """The repository side of a delta run."""

    def digests(self) -> Mapping[str, str]:
        """Path to ``sha256:`` blob digest for every current file; reads no content."""
        ...

    def read(self, path: str) -> bytes:
        """The bytes of one file; holds a parse resource until released."""
        ...

    def release(self, paths: tuple[str, ...]) -> None:
        """Release the resources held for ``paths``."""
        ...

    def unreleased(self) -> int:
        """How many resources are still held."""
        ...


class FileResolution(BaseModel):
    """One re-indexed file and its type-resolution outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    language: str | None
    resolution: Literal["resolved", "syntax_only", "unsupported", "failed"]
    reason: str = ""


class IndexPage(BaseModel):
    """The evidence of one delta run (SCION-SOURCE-INGEST-R004.17)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    changed_files_digest: str = Field(pattern=_SHA)
    parser_versions: dict[str, str]
    semantic_mode: Literal["syntax", "typed"]
    layout_revision: str = Field(min_length=1)
    languages: dict[str, int]
    peak_resident_bytes: int = Field(ge=0)
    released_at: datetime
    files: tuple[FileResolution, ...]

    def state(self) -> RepositoryIndexState:
        """The durable state this page leaves behind."""
        return RepositoryIndexState(
            repository=self.repository,
            sequence=self.sequence,
            changed_files_digest=self.changed_files_digest,
            parser_versions=self.parser_versions,
            semantic_mode=self.semantic_mode,
            layout_revision=self.layout_revision,
            peak_resident_bytes=self.peak_resident_bytes,
            released_at=self.released_at,
        )


@dataclass(frozen=True)
class _Plan:
    changed: tuple[str, ...]
    removed: tuple[str, ...]


def _changed(current: Mapping[str, str], known: Mapping[str, str]) -> _Plan:
    changed = tuple(sorted(p for p, d in current.items() if known.get(p) != d))
    removed = tuple(sorted(set(known) - set(current)))
    return _Plan(changed, removed)


def _changed_digest(plan: _Plan, current: Mapping[str, str]) -> str:
    body = json.dumps(
        [[p, current[p]] for p in plan.changed] + [[p, ""] for p in plan.removed],
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(body.encode()).hexdigest()}"


def _language(path: str) -> str | None:
    return LANGUAGE_MATRIX.get(PurePosixPath(path).suffix)


async def index_delta(
    client: MedullaClient,
    *,
    repository: str,
    source: DeltaIndexSource,
    known_digests: Mapping[str, str],
    previous: RepositoryIndexState | None,
    config: DeltaIndexConfig,
    layout_revision: str,
    now: Callable[[], datetime],
    parser_versions: Mapping[str, str] | None = None,
    submit: Callable[
        [MedullaClient, tuple[RepositoryFile, ...]],
        object,
    ]
    | None = None,
) -> IndexPage:
    """Index only the files whose digest differs from ``known_digests``.

    Raises:
        IndexRefusal: ``INDEX_TYPE_MODE_INVALID`` for an unknown mode,
            ``INDEX_MEMORY_EXCEEDED`` past ``peak_bytes`` and
            ``INDEX_RELEASE_PENDING`` while a resource is unreleased at completion.
            A file outside the language matrix or one the engine reports failed
            blocks no other file; it appears in the page with its reason.
    """
    if config.semantic_mode not in ("syntax", "typed"):
        raise IndexRefusal("INDEX_TYPE_MODE_INVALID", config.semantic_mode)
    current = source.digests()
    plan = _changed(current, known_digests)
    sender = submit or submit_repository_batch
    outcomes: list[FileResolution] = []
    languages: dict[str, int] = {}
    peak = 0
    sendable = [p for p in plan.changed if _language(p) is not None]
    for path in plan.changed:
        if _language(path) is None:
            outcomes.append(
                FileResolution(
                    path=path,
                    language=None,
                    resolution="unsupported",
                    reason="INDEX_LANGUAGE_UNSUPPORTED",
                )
            )
    for start in range(0, len(sendable), config.batch_files):
        paths = tuple(sendable[start : start + config.batch_files])
        files: list[RepositoryFile] = []
        held = 0
        try:
            for path in paths:
                content = source.read(path)
                held += len(content)
                if held > config.peak_bytes:
                    raise IndexRefusal(
                        "INDEX_MEMORY_EXCEEDED", f"{held} > {config.peak_bytes}"
                    )
                files.append(
                    RepositoryFile(
                        path=path, blob_digest=current[path], content=content
                    )
                )
            peak = max(peak, held)
            receipt = await _await(sender(client, tuple(files)))
        finally:
            source.release(paths)
        outcomes.extend(_resolutions(receipt, config.semantic_mode, languages))
    if source.unreleased():
        raise IndexRefusal("INDEX_RELEASE_PENDING", str(source.unreleased()))
    sequence = (previous.sequence + 1) if previous else 1
    page = IndexPage(
        repository=repository,
        sequence=sequence,
        changed_files_digest=_changed_digest(plan, current),
        parser_versions=dict(parser_versions or {}),
        semantic_mode=config.semantic_mode,
        layout_revision=layout_revision,
        languages=languages,
        peak_resident_bytes=peak,
        released_at=now(),
        files=tuple(sorted(outcomes, key=lambda item: item.path)),
    )
    return page


def accept_index_page(
    raw: object, previous: RepositoryIndexState | None
) -> RepositoryIndexState:
    """Validate a connector's index page and return the state it leaves.

    Raises:
        IndexRefusal: ``INDEX_SCHEMA_MISMATCH`` for a malformed page and
            ``INDEX_SEQUENCE_STALE`` when the sequence does not grow, in which
            case the checkpoint must not advance.
    """
    try:
        state = IndexPage.model_validate(raw).state()
    except ValueError as exc:
        raise IndexRefusal("INDEX_SCHEMA_MISMATCH", "index page") from exc
    try:
        state.follows(previous)
    except ValueError as exc:
        raise IndexRefusal("INDEX_SEQUENCE_STALE", str(exc)) from exc
    return state


async def _await(value: object) -> RepositoryBatchReceipt:
    import inspect

    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, RepositoryBatchReceipt):
        raise IndexRefusal("INDEX_SCHEMA_MISMATCH", "batch receipt")
    return value


def _resolutions(
    receipt: RepositoryBatchReceipt,
    mode: str,
    languages: dict[str, int],
) -> list[FileResolution]:
    try:
        engine = receipt.result.file_outcomes
    except AttributeError as exc:
        raise IndexRefusal("INDEX_SCHEMA_MISMATCH", "file_outcomes") from exc
    out: list[FileResolution] = []
    for path, outcome in zip(receipt.paths, engine, strict=True):
        language = _language(path)
        status = getattr(outcome.status, "value", outcome.status)
        if language is not None:
            languages[language] = languages.get(language, 0) + 1
        if status == "success":
            done: Literal["resolved", "syntax_only"] = (
                "resolved" if mode == "typed" else "syntax_only"
            )
            out.append(FileResolution(path=path, language=language, resolution=done))
        else:
            reason = "; ".join(str(getattr(d, "code", d)) for d in outcome.diagnostics)
            out.append(
                FileResolution(
                    path=path,
                    language=language,
                    resolution="unsupported" if status == "unsupported" else "failed",
                    reason=reason or str(status),
                )
            )
    return out
