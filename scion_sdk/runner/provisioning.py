"""One provisioning pass: build the content pack and import it when it changed."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from medulla.generated.connector_pack import (
    PackImportResultImported,
    PackImportResultRejected,
)

from scion_sdk.artifacts.pack import (
    build_connector_content_pack,
    build_content_pack,
)
from scion_sdk.manifest.release_digest import ReleaseDigestError, read_release_evidence
from scion_sdk.ports.artifact_kind import ArtifactKind
from scion_sdk.ports.release_pins import ReleasePins
from scion_sdk.ports.session import McpSession
from scion_sdk.ports.sink import CapturedConnectorPack, Sink
from scion_sdk.runner.errors import ReleaseVerificationError, SinkReceiptError
from scion_sdk.runner.logs import structured

if TYPE_CHECKING:
    from scion_sdk.mcp.content import ConnectorContent

__all__ = [
    "ProvisionOutcome",
    "provision_connector_content",
    "provision_content",
    "verify_release",
]

_logger = logging.getLogger(__name__)
_RELEASE_URI = "connector://release"


@dataclass(frozen=True)
class ProvisionOutcome:
    """What one provisioning pass did."""

    pack_digest: str
    changed: bool
    imported: int
    resource_uris: frozenset[str]


async def provision_content(
    session: McpSession,
    *,
    connector: str,
    kinds: Sequence[ArtifactKind],
    sink: Sink,
    pins: ReleasePins,
) -> ProvisionOutcome:
    """Read the served content as a pack; import it only when its digest changed.

    EG computes and owns the pack digest after the current catalog binding is
    resolved. The outcome names the MCP resource URIs a change subscription can
    follow.

    The release digest is verified first and fails closed, so an unverified
    connector imports no pack (SCION-CONNECTOR-CONTROL-R650.1.3).

    Raises:
        ReleaseVerificationError: the digest is unreported, invalid, unpinned or differs.
        SinkReceiptError: the receipt names another pack.
    """
    await verify_release(session, connector, pins)
    pack = await build_content_pack(session, connector=connector, kinds=kinds)
    return await _import_pack(pack, sink)


async def verify_release(
    session: McpSession, connector: str, pins: ReleasePins
) -> None:
    """Check the wheel content digest the connector reports against the release pin."""
    resources = await session.list_resources()
    if _RELEASE_URI not in {str(getattr(r, "uri", "")) for r in resources}:
        raise ReleaseVerificationError("RELEASE_DIGEST_UNREPORTED")
    try:
        evidence = read_release_evidence(await session.read_resource(_RELEASE_URI))
    except ReleaseDigestError:
        raise ReleaseVerificationError("RELEASE_EVIDENCE_INVALID") from None
    try:
        pinned = await pins.pinned_digests(evidence.distribution, evidence.version)
    except Exception:
        raise ReleaseVerificationError("RELEASE_PIN_UNAVAILABLE") from None
    if not pinned:
        raise ReleaseVerificationError("RELEASE_PIN_MISSING")
    if evidence.pin not in pinned:
        raise ReleaseVerificationError("RELEASE_DIGEST_MISMATCH")
    _logger.info(
        "connector %s release_verified %s",
        connector,
        evidence.content_sha256,
        extra=structured(
            "release_verified",
            connector,
            version=evidence.version,
            digest=evidence.content_sha256,
        ),
    )


async def provision_connector_content(
    content: ConnectorContent, *, sink: Sink
) -> ProvisionOutcome:
    """Capture and import one explicitly composed content provider.

    Calling this once per provider preserves independent ConnectorPack heads
    and receipts even when their resources are served by one Arbora process.
    """
    return await _import_pack(await build_connector_content_pack(content), sink)


async def _import_pack(pack: CapturedConnectorPack, sink: Sink) -> ProvisionOutcome:
    result = await sink.import_pack(pack)
    if isinstance(result, PackImportResultRejected):
        raise SinkReceiptError("ConnectorPack import was rejected")
    if isinstance(result, PackImportResultImported):
        counts = result.receipt.counts
        imported = counts.published + counts.republished + counts.revised
        return ProvisionOutcome(
            result.receipt.pack_digest, True, imported, pack.resource_uris
        )
    return ProvisionOutcome(result.pack_digest, False, 0, pack.resource_uris)
