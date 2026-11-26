"""The ``SourceAdapter`` port: extracts records from one source stream."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from medulla.generated.source_ingestion import SourceCheckpoint

from scion_sdk.contracts import (
    CapabilityDescriptor,
    ReconciliationReport,
    RecordPage,
    StreamDescriptor,
)
from scion_sdk.ports.session import McpSession
from scion_sdk.repository.indexing import RepositoryIndexState

__all__ = ["IndexStateAdapter", "SourceAdapter"]


@runtime_checkable
class SourceAdapter(Protocol):
    """Describe, verify, extract page by page, and reconcile one stream."""

    kind: str

    def describe(self) -> CapabilityDescriptor:
        """Declare the adapter's capabilities; must not perform I/O."""
        ...

    async def discover(self, session: McpSession) -> StreamDescriptor:
        """Verify the live source contract before any extraction."""
        ...

    async def extract(
        self, session: McpSession, checkpoint: SourceCheckpoint | None
    ) -> RecordPage:
        """Extract after EG's accepted checkpoint (``None`` is initial CAS)."""
        ...

    async def reconcile(
        self, session: McpSession, known_ids: frozenset[str]
    ) -> ReconciliationReport:
        """Compare ``known_ids`` with the ids the source currently serves."""
        ...


@runtime_checkable
class IndexStateAdapter(Protocol):
    """An adapter whose pages carry a delta index state (SCION-SOURCE-INGEST-R004.15).

    The accepted checkpoint is the durable carrier: the previous state rides in
    its position and the next extract receives it from there.
    """

    def index_state(self) -> RepositoryIndexState | None:
        """The state the last extracted page left, or ``None`` before any."""
        ...
