"""The provider-side port for repository file effects (SCION-GOVERNED-WRITEBACK-R004.4).

The port is the provider's business API and nothing else: it offers
``commit_file`` and has no storage method. A provider that rejects the
request for a business reason raises :class:`RepositoryBusinessRuleRejected`;
the SDK transport maps that refusal to the stable code
``BUSINESS_RULE_REJECTED`` and tries no other path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

__all__ = [
    "RepositoryBusinessRuleRejected",
    "RepositoryCommitReceipt",
    "RepositoryCommitRequest",
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


@dataclass(frozen=True, slots=True)
class RepositoryCommitRequest:
    """One business-API commit addressed by the provider's own operation name."""

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
    method and no unconditional fallback.
    """

    async def commit_file(
        self, operation: str, commit: RepositoryCommitRequest
    ) -> RepositoryCommitReceipt:
        """Apply one file commit through the provider's business API."""
        ...
