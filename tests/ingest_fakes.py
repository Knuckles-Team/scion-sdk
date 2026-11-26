"""Shared in-memory ingest transport base for ingest tests."""

from __future__ import annotations

from medulla.generated.source_ingestion import (
    SourceIngestionReceipt,
    SourceIngestionRequest,
    SourceIngestStatus,
)

from scion_sdk.ingest import AcceptedContract, ContractSource, IngestBinding
from scion_sdk.testing.sinks import InMemorySink

CONTRACT_DIGEST = "a" * 64
CONTRACT = ContractSource(digest=CONTRACT_DIGEST)


def bind(connector: str, stream: str, **fields: object) -> IngestBinding:
    """A binding under the accepted test contract."""
    return CONTRACT.bind(connector, stream, **fields)  # type: ignore[arg-type]


class SinkBackedEngine:
    """Records submissions and delegates them to an in-memory sink."""

    def __init__(self) -> None:
        self.sink = InMemorySink()
        self.requests: list[SourceIngestionRequest] = []

    def _gate(self) -> None:
        """Raise here to refuse a submission after it is recorded."""

    async def submit(self, request: SourceIngestionRequest) -> SourceIngestionReceipt:
        self.requests.append(request)
        self._gate()
        return await self.sink.submit(request)

    async def accepted_contract(self, connector: str) -> AcceptedContract | None:
        return AcceptedContract(CONTRACT_DIGEST)

    async def source_status(self, connector: str, stream: str) -> SourceIngestStatus:
        return await self.sink.source_status(connector, stream)


class AcceptingSink(InMemorySink):
    """An in-memory sink whose engine has accepted the test contract."""

    async def accepted_contract(self, connector: str) -> AcceptedContract | None:
        return AcceptedContract(CONTRACT_DIGEST)
