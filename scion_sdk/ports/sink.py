"""The ``Sink`` port: durably accepts record batches and content packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from medulla.connector_pack import ConnectorPackArchive
from medulla.generated.connector_pack import PackImportResult
from medulla.generated.source_ingestion import (
    SourceIngestionReceipt,
    SourceIngestionRequest,
    SourceIngestStatus,
)

__all__ = ["CapturedConnectorPack", "Sink", "SinkReadiness"]


@dataclass(frozen=True)
class CapturedConnectorPack:
    """SDK capture metadata around one generated, immutable pack archive."""

    connector: str
    server_package_version: str
    archive: ConnectorPackArchive
    resource_uris: frozenset[str]


@dataclass(frozen=True)
class SinkReadiness:
    """Whether a sink can commit ingestion right now.

    ``reason`` is set whenever ``ready`` is ``False`` and is safe to publish
    (a health check body, a log line): implementations must never put a
    credential or other secret value in it.
    """

    ready: bool
    reason: str | None = None


@runtime_checkable
class Sink(Protocol):
    """The ingestion authority seen from the SDK."""

    async def submit(self, batch: SourceIngestionRequest) -> SourceIngestionReceipt:
        """Commit a record batch; the receipt is returned only after commit."""
        ...

    async def source_status(self, connector: str, stream: str) -> SourceIngestStatus:
        """Read EG's sole durable checkpoint and live-set status."""
        ...

    async def import_pack(
        self, pack: CapturedConnectorPack, *, allow_mass_withdrawal: bool = False
    ) -> PackImportResult:
        """Import a capture through EG's generated ConnectorPack contract.

        ``allow_mass_withdrawal`` is the operator's decision to publish a pack
        that withdraws more than half of the published surface, for example a
        connector moving to one tool per operation. EG requires the stronger
        ``admin:connector-pack`` action for it.
        """
        ...

    async def readiness(self) -> SinkReadiness:
        """Whether this sink can commit right now, without side effects."""
        ...
