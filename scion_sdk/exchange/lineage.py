"""Lineage records for published artifacts (SCION-CENTRALIZED-CAPABILITIES-R043.6).

When ``SCION_EXCHANGE_LINEAGE`` is on, one change set goes through the
existing ingest facade: a ``MediaFile`` per artifact, an ``Action`` for the
run, ``participatedIn`` from each output to the run, ``hasInput`` from the
run to each staged input, and ``derivedFrom`` from each output to each input.
The engine core already defines every class and relation used here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scion_sdk.ingest import (
    ChangeSet,
    Entity,
    IngestBinding,
    Relationship,
    current_ingest,
)

if TYPE_CHECKING:
    from scion_sdk.exchange.publish import PublishContext, PublishReport
    from scion_sdk.ingest import KnowledgeIngest

__all__ = [
    "EXCHANGE_LINEAGE_FAILED",
    "StagedInput",
    "lineage_change_set",
    "record_lineage",
]

EXCHANGE_LINEAGE_FAILED = "EXCHANGE_LINEAGE_FAILED"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StagedInput:
    """One input the run read: its source reference, name and digest."""

    reference: str
    name: str
    sha256: str
    size: int


def _artifact_id(sha256: str) -> str:
    return f"artifact:sha256:{sha256}"


def lineage_change_set(
    report: PublishReport, inputs: list[StagedInput], context: PublishContext
) -> ChangeSet:
    """The change set that records one run, its outputs and its inputs."""
    run_id = f"run:{context.namespace}:{report.run_id}"
    entities = [
        Entity(
            id=run_id,
            node_type="Action",
            properties={
                "tool": context.tool,
                "namespace": context.namespace,
                "run": report.run_id,
                "caller": context.caller,
            },
        )
    ]
    relationships: list[Relationship] = []
    for item in inputs:
        entities.append(
            Entity(
                id=_artifact_id(item.sha256),
                node_type="MediaFile",
                properties={
                    "contentSha256": item.sha256,
                    "sizeBytes": item.size,
                    "fileName": item.name,
                    "locations": [item.reference],
                },
            )
        )
        relationships.append(
            Relationship(
                source=run_id, target=_artifact_id(item.sha256), relationship="hasInput"
            )
        )
    for artifact in report.artifacts:
        if not artifact.sha256:
            continue
        output = _artifact_id(artifact.sha256)
        locations = [
            f"exchange://{loc.sink}/{loc.key}"
            for loc in artifact.locations
            if loc.code is None
        ]
        entities.append(
            Entity(
                id=output,
                node_type="MediaFile",
                properties={
                    "contentSha256": artifact.sha256,
                    "sizeBytes": artifact.size,
                    "fileName": artifact.name,
                    "encodingFormat": artifact.media_type,
                    "locations": locations,
                },
            )
        )
        relationships.append(
            Relationship(source=output, target=run_id, relationship="participatedIn")
        )
        relationships.extend(
            Relationship(
                source=output, target=_artifact_id(i.sha256), relationship="derivedFrom"
            )
            for i in inputs
        )
    return ChangeSet(entities=tuple(entities), relationships=tuple(relationships))


async def record_lineage(
    report: PublishReport,
    inputs: list[StagedInput],
    context: PublishContext,
    *,
    connector: str,
    ingest: KnowledgeIngest | None = None,
) -> str | None:
    """Submit the lineage change set; return a refusal code on failure, else ``None``."""
    try:
        service = ingest or current_ingest()
        binding = IngestBinding(connector=connector, stream="artifacts")
        await service.submit(binding, lineage_change_set(report, inputs, context))
    except Exception:  # lineage never fails the call; the code reports it
        _logger.warning(
            "artifact lineage was not recorded: %s", EXCHANGE_LINEAGE_FAILED
        )
        return EXCHANGE_LINEAGE_FAILED
    return None
