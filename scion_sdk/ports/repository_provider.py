"""The provider-side port for repository file effects (SCION-GOVERNED-WRITEBACK-R004.4).

The port is the provider's business API and nothing else: it offers
``commit_file`` and has no storage method. A provider that rejects the
request for a business reason raises :class:`RepositoryBusinessRuleRejected`;
the SDK transport maps that refusal to the stable code
``BUSINESS_RULE_REJECTED`` and tries no other path.

A commit is conditional (SCION-GOVERNED-WRITEBACK-R004.8): the provider
applies it atomically only while the target ref still points at the base
revision and the file still matches the expected digest and mode. A
provider that cannot offer this condition is not admitted:
``supports_conditional_update`` must be true at registration. There is
no force option and no unconditional fallback.

The port also offers ``read_file`` (SCION-GOVERNED-WRITEBACK-R004.10):
an independent read of one file at an exact revision, used only as
readback after a commit — never as the mutation response body and never
from an ingestion cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, runtime_checkable

__all__ = [
    "BaseStale",
    "ConditionalCommit",
    "FileStateMismatch",
    "RepositoryBusinessRuleRejected",
    "RepositoryCommitReceipt",
    "RepositoryFileRead",
    "RepositoryProviderPort",
    "RepositoryTreeKind",
]

#: The kind of one entry of the immutable tree at a pinned base revision.
RepositoryTreeKind = Literal["file", "tree", "symlink", "submodule"]


class RepositoryBusinessRuleRejected(RuntimeError):
    """The provider's business API rejected the request for a business reason.

    The rejection is final for this request; no storage fallback or retry is
    permitted.
    """

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or "business rule rejected")


class ConditionalCommitRefused(RuntimeError):
    """A refused atomic condition of one conditional commit."""

    code: ClassVar[str]


class BaseStale(ConditionalCommitRefused):
    """The target ref no longer points at the commit's base revision."""

    code = "BASE_STALE"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or "target ref moved off the base revision")


class FileStateMismatch(ConditionalCommitRefused):
    """The file no longer matches the expected digest or mode."""

    code = "FILE_STATE_MISMATCH"

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            detail or "file state no longer matches the expected digest or mode"
        )


@dataclass(frozen=True, slots=True)
class ConditionalCommit:
    """One conditional business-API commit addressed by the provider's operation.

    The idempotency key travels as the commit trailer ``Effect-Key`` so a
    restarted process can find the commit in the target ref history.
    """

    repository_id: str
    target_ref: str
    relative_path: str
    base_revision: str
    expected_file_digest: str | None
    expected_executable: bool | None
    payload: bytes
    executable: bool
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RepositoryFileRead:
    """One independent file read at an exact revision."""

    data: bytes
    executable: bool


@dataclass(frozen=True, slots=True)
class RepositoryCommitReceipt:
    """The provider's acknowledgement of one applied commit.

    The receipt names the business operation it executed; the transport
    refuses an acknowledgement that names any other operation.
    """

    business_api_operation: str
    effect_revision: str


@runtime_checkable
class RepositoryProviderPort(Protocol):
    """One provider's business API for repository file effects.

    The port offers ``commit_file`` and nothing else: there is no storage
    method and no unconditional fallback. A provider that cannot offer the
    atomic commit condition is not admitted.
    """

    supports_conditional_update: bool

    async def commit_file(
        self, operation: str, commit: ConditionalCommit
    ) -> RepositoryCommitReceipt:
        """Apply one file commit atomically through the provider's business API."""
        ...

    async def read_file(
        self, repository_id: str, revision: str, relative_path: str
    ) -> RepositoryFileRead | None:
        """Read one file independently at an exact revision, or None if absent."""
        ...
