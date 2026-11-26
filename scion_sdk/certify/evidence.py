"""Submit a passing certification as engine conformance evidence.

SCION-CENTRALIZED-CAPABILITIES-R041. The engine writes a ``ConformanceEvidence``
record in the connector pack import commit (``MEDULLA-TYPED-PACKS-R807``): one
fingerprint per served tool, the pack digest and the immutable revision. The SDK
therefore submits by importing the certified pack through the Medulla client
adapter, then reads the record back and checks that it binds the certified
connector, the imported pack digest and every certified tool.
"""

from __future__ import annotations

from typing import Any, Protocol

from medulla.generated.connector_pack import (
    ConformanceEvidence,
    ConformanceEvidenceState,
    ConnectorPackConformanceEvidenceRequest,
    ConnectorPackOpConformanceEvidence,
)
from medulla.generated.storage import send_connector_pack

from scion_sdk.certify.certification import CertificationReport
from scion_sdk.certify.verdicts import ToolStatus
from scion_sdk.ports.sink import CapturedConnectorPack

__all__ = [
    "EvidenceRefused",
    "EvidenceSubmitter",
    "read_conformance_evidence",
    "submit_certification_evidence",
]


class EvidenceRefused(RuntimeError):
    """Evidence was not submitted or not found; ``reason`` is a closed code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EvidenceSubmitter(Protocol):
    """Submits one passing certification report; returns the stored evidence."""

    async def __call__(self, report: CertificationReport) -> ConformanceEvidence: ...


class _PackImporter(Protocol):
    async def import_pack(self, pack: CapturedConnectorPack) -> Any: ...


async def read_conformance_evidence(
    client: Any, *, tenant_id: str, connector: str
) -> ConformanceEvidence:
    """Read the current evidence; an absent, expired or revoked record refuses."""
    op = ConnectorPackOpConformanceEvidence(
        op="conformance_evidence",
        request=ConnectorPackConformanceEvidenceRequest(
            connector=connector, tenant_id=tenant_id
        ),
    )
    try:
        result = await send_connector_pack(client, {"op": op.model_dump(mode="json")})
    except Exception:
        raise EvidenceRefused("evidence_unavailable") from None
    try:
        evidence = ConformanceEvidence.model_validate(result.payload)
    except ValueError:
        raise EvidenceRefused("evidence_malformed") from None
    if evidence.state is not ConformanceEvidenceState.ACTIVE:
        raise EvidenceRefused("evidence_revoked")
    return evidence


async def submit_certification_evidence(
    report: CertificationReport,
    *,
    sink: _PackImporter,
    client: Any,
    pack: CapturedConnectorPack,
    tenant_id: str,
) -> ConformanceEvidence:
    """Import the certified pack, then read back and verify its evidence."""
    certified = {
        verdict.tool
        for verdict in report.verdicts
        if verdict.status is ToolStatus.CERTIFIED
    }
    if not report.passed or not certified:
        raise EvidenceRefused("report_not_passing")
    if pack.connector != report.connector:
        raise EvidenceRefused("pack_connector_mismatch")
    result = await sink.import_pack(pack)
    evidence = await read_conformance_evidence(
        client, tenant_id=tenant_id, connector=report.connector
    )
    pack_digest = getattr(result, "pack_digest", None) or getattr(
        getattr(result, "receipt", None), "pack_digest", None
    )
    if (
        evidence.connector != report.connector
        or evidence.tenant_id != tenant_id
        or evidence.pack_digest != pack_digest
        or not certified <= set(evidence.tool_fingerprints)
    ):
        raise EvidenceRefused("evidence_mismatch")
    return evidence
