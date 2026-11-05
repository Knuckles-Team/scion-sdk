"""Tool results carry resource links (SCION-CENTRALIZED-CAPABILITIES-R043.5).

The upstream content stays first and unchanged. The exchange appends one MCP
``resource_link`` block per published artifact and lists every artifact under
the result metadata key ``scion/artifacts``. A link names a signed URL when a
sink minted one, else ``exchange://<sink>/<key>``; no credential appears.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_types import ResourceLink

from scion_sdk.exchange.publish import EXCHANGE_PUBLISH_FAILED

if TYPE_CHECKING:
    from scion_sdk.exchange.publish import PublishedArtifact, PublishReport

__all__ = [
    "ARTIFACTS_META_KEY",
    "EXCHANGE_META_KEY",
    "artifact_link",
    "attach_artifacts",
]

ARTIFACTS_META_KEY = "scion/artifacts"
EXCHANGE_META_KEY = "scion/exchange"


def artifact_link(artifact: PublishedArtifact) -> ResourceLink | None:
    """The link block of one artifact, or ``None`` when no sink holds it."""
    held = [loc for loc in artifact.locations if loc.code is None]
    if not held:
        return None
    signed = next((loc.url for loc in held if loc.url), None)
    uri = signed or f"exchange://{held[0].sink}/{held[0].key}"
    return ResourceLink(
        name=artifact.name.rsplit("/", 1)[-1],
        title=artifact.name,
        uri=uri,
        mime_type=artifact.media_type,
        size=artifact.size,
    )


def attach_artifacts(
    result: Any, report: PublishReport, codes: list[str] | None = None
) -> Any:
    """Append links and metadata for ``report`` to a tool result in place."""
    content = list(result.content or [])
    for artifact in report.artifacts:
        link = artifact_link(artifact)
        if link is not None:
            content.append(link)
    result.content = content
    meta = dict(result.meta or {})
    meta[ARTIFACTS_META_KEY] = [
        a.model_dump(exclude_none=True) for a in report.artifacts
    ]
    found = list(codes or [])
    if not report.ok:
        found.append(EXCHANGE_PUBLISH_FAILED)
    if found:
        meta[EXCHANGE_META_KEY] = {"run": report.run_id, "codes": found}
    else:
        meta[EXCHANGE_META_KEY] = {"run": report.run_id}
    result.meta = meta
    return result
