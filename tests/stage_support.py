"""Shared fixtures for the concurrent-stage tests."""

from __future__ import annotations

from typing import Any

import anyio
from medulla.generated.source_ingestion import (
    SourceCheckpoint,
    SourceIngestionMode,
    SourceRecord,
    SourceRecordProvenance,
)

from scion_sdk.contracts import (
    CapabilityDescriptor,
    ReconciliationReport,
    RecordPage,
    StreamDescriptor,
)
from scion_sdk.runner.stages import StageDependency, StageDescriptor, StageLimits
from scion_sdk.schema_drift import SchemaContract

CONTRACT = SchemaContract.from_schema(
    {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}},
    identifier_fields=("id",),
)
CONNECTOR = "stage-connector"
TENANT = "tenant-a"


def limits(max_active: int = 2, deadline_ms: int = 60_000) -> StageLimits:
    return StageLimits(
        max_active_stages=max_active,
        page_rows=100,
        page_bytes=4096,
        queued_pages=1,
        deadline_ms=deadline_ms,
    )


def stage(
    stage_id: str,
    *deps: tuple[str, str],
    max_active: int = 2,
    deadline_ms: int = 60_000,
) -> StageDescriptor:
    return StageDescriptor(
        stage_id=stage_id,
        tenant=TENANT,
        connector=CONNECTOR,
        source=stage_id,
        stream=stage_id,
        manifest_digest="sha256:" + "b" * 64,
        dependencies=tuple(StageDependency(stage_id=s, receipt_id=r) for s, r in deps),
        admitted_limits=limits(max_active, deadline_ms),
    )


class StageAdapter:
    """One stream, one page; ``hook`` runs inside ``extract``."""

    kind = "stage"

    def __init__(self, stream: str, hook: Any = None) -> None:
        self.stream = stream
        self.hook = hook
        self.extracted = 0
        self.seen: list[SourceCheckpoint | None] = []

    def describe(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(kind=self.kind)

    async def discover(self, session: object) -> StreamDescriptor:
        return StreamDescriptor(
            stream=self.stream,
            tool="read",
            schema_sha256="0" * 64,
            schema_contract=CONTRACT,
        )

    async def extract(
        self, session: object, checkpoint: SourceCheckpoint | None
    ) -> RecordPage:
        self.seen.append(checkpoint)
        self.extracted += 1
        if self.hook is not None:
            await self.hook(self)
        await anyio.sleep(0)
        if checkpoint is not None:
            return RecordPage(
                records=(),
                mode=SourceIngestionMode.DELTA,
                strict_schema=False,
                checkpoint=checkpoint,
                exhausted=True,
                schema_contract=CONTRACT,
            )
        return RecordPage(
            records=(
                SourceRecord(
                    stream=self.stream,
                    record_id=f"{self.stream}-1",
                    mapping_reference=f"connector:{CONNECTOR}",
                    payload={"id": f"{self.stream}-1"},
                    provenance=SourceRecordProvenance(
                        connector=CONNECTOR,
                        adapter_kind=self.kind,
                        server=CONNECTOR,
                        tool="read",
                        tool_schema_sha256="0" * 64,
                        source_uri=f"stage://{self.stream}/1",
                    ),
                ),
            ),
            mode=SourceIngestionMode.DELTA,
            strict_schema=False,
            checkpoint=SourceCheckpoint(stream=self.stream, position={"page": 1}),
            exhausted=True,
            schema_contract=CONTRACT,
        )

    async def reconcile(
        self, session: object, known_ids: frozenset[str]
    ) -> ReconciliationReport:
        return ReconciliationReport(
            stream=self.stream, missing_from_source=(), unknown_to_sink=()
        )
