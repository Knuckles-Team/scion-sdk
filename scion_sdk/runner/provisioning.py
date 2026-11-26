"""One provisioning pass: build the content pack and import it when it changed."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
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
from scion_sdk.artifacts.release_inputs import read_release_inputs
from scion_sdk.manifest.release_signature import (
    ReleaseSigningError,
    read_release_evidence,
)
from scion_sdk.manifest.release_signature import verify_release as _verify_signature
from scion_sdk.ports.artifact_kind import ArtifactKind
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
    trusted_keys: Mapping[str, bytes],
) -> ProvisionOutcome:
    """Read the served content as a pack; import it only when its digest changed.

    EG computes and owns the pack digest after the current catalog binding is
    resolved. The outcome names the MCP resource URIs a change subscription can
    follow.

    The signed release is verified first and fails closed, so an unverified
    connector imports no pack (SCION-CONNECTOR-CONTROL-R650.1.3).

    Raises:
        ReleaseVerificationError: the release is unsigned, untrusted, altered or invalid.
        SinkReceiptError: the receipt names another pack.
    """
    await verify_release(session, connector, trusted_keys)
    pack = await build_content_pack(session, connector=connector, kinds=kinds)
    return await _import_pack(pack, sink)


async def verify_release(
    session: McpSession, connector: str, trusted_keys: Mapping[str, bytes]
) -> None:
    """Check the served release evidence against the live inputs and the lock."""
    resources = await session.list_resources()
    if _RELEASE_URI not in {str(getattr(r, "uri", "")) for r in resources}:
        raise ReleaseVerificationError("RELEASE_UNSIGNED")
    try:
        evidence = read_release_evidence(await session.read_resource(_RELEASE_URI))
    except ReleaseSigningError:
        raise ReleaseVerificationError("RELEASE_SIGNATURE_INVALID") from None
    if evidence.key_id not in trusted_keys:
        raise ReleaseVerificationError("RELEASE_KEY_UNTRUSTED")
    try:
        declaration, graph = await read_release_inputs(session)
        valid = _verify_signature(
            declaration, graph, evidence, trusted_keys=trusted_keys
        )
        digest_ok = evidence.digest == _live_digest(declaration, graph)
    except ValueError:
        raise ReleaseVerificationError("RELEASE_SIGNATURE_INVALID") from None
    if not digest_ok:
        raise ReleaseVerificationError("RELEASE_DIGEST_MISMATCH")
    if not valid:
        raise ReleaseVerificationError("RELEASE_SIGNATURE_INVALID")
    _logger.info(
        "connector %s release_verified %s",
        connector,
        evidence.digest,
        extra=structured(
            "release_verified",
            connector,
            key_id=evidence.key_id,
            digest=evidence.digest,
        ),
    )


def _live_digest(declaration: object, graph: object) -> str:
    from scion_sdk.manifest.release_identity import release_identity_digest

    return release_identity_digest(declaration, graph)  # type: ignore[arg-type]


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
