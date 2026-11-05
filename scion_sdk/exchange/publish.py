"""Output publication to every sink (SCION-CENTRALIZED-CAPABILITIES-R043.4).

Each output file of a run is hashed, sized and typed, then written to every
configured sink at the sink's rendered key. A failed write records a code for
that location only; a failed ``required`` sink marks the whole report failed.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from scion_sdk.exchange.config import render_key
from scion_sdk.storage.base import NotFoundError, StorageError

if TYPE_CHECKING:
    from scion_sdk.exchange.config import ExchangeSettings, SinkDecl
    from scion_sdk.exchange.sinks import SinkBackends
    from scion_sdk.exchange.staging import RunStaging
    from scion_sdk.storage.base import ObjectStoreBackend

__all__ = [
    "EXCHANGE_OUTPUT_LIMIT",
    "EXCHANGE_OUTPUT_TOO_LARGE",
    "EXCHANGE_PUBLISH_FAILED",
    "ArtifactLocation",
    "PublishContext",
    "PublishReport",
    "PublishedArtifact",
    "digest_file",
    "publish_outputs",
]

EXCHANGE_OUTPUT_TOO_LARGE = "EXCHANGE_OUTPUT_TOO_LARGE"
EXCHANGE_OUTPUT_LIMIT = "EXCHANGE_OUTPUT_LIMIT"
EXCHANGE_PUBLISH_FAILED = "EXCHANGE_PUBLISH_FAILED"
_CHUNK = 1024 * 1024


class ArtifactLocation(BaseModel):
    """Where one sink holds one artifact."""

    model_config = ConfigDict(frozen=True)

    sink: str
    key: str
    url: str | None = None
    code: str | None = None


class PublishedArtifact(BaseModel):
    """One output file and every location that holds it."""

    model_config = ConfigDict(frozen=True)

    name: str
    sha256: str
    size: int
    media_type: str
    locations: tuple[ArtifactLocation, ...] = ()
    code: str | None = None


@dataclass(frozen=True)
class PublishContext:
    """Who produced the outputs: the namespace, tool and caller of one run."""

    namespace: str
    tool: str
    caller: str = "anonymous"


@dataclass
class PublishReport:
    """The artifacts of one run and whether every required sink holds them."""

    run_id: str
    artifacts: list[PublishedArtifact] = field(default_factory=list)
    ok: bool = True


def digest_file(path: Path) -> str:
    """The SHA-256 hex digest of one file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signed_url(backend: ObjectStoreBackend, sink: SinkDecl, key: str) -> str | None:
    if sink.signed_url_ttl_s <= 0:
        return None
    if not backend.capabilities().get("presigned_urls"):
        return None
    return backend.presigned_url(sink.bucket, key, "GET", sink.signed_url_ttl_s)


def _write(
    sink: SinkDecl,
    backends: SinkBackends,
    key: str,
    payload: tuple[bytes, str, dict[str, str]],
) -> ArtifactLocation:
    data, media_type, metadata = payload
    try:
        backend = backends.backend(sink.store)
        try:
            backend.put_object(sink.bucket, key, data, media_type, metadata)
        except NotFoundError:  # a fresh sink: create its bucket once, then retry
            backend.create_bucket(sink.bucket)
            backend.put_object(sink.bucket, key, data, media_type, metadata)
        return ArtifactLocation(
            sink=sink.name, key=key, url=_signed_url(backend, sink, key)
        )
    except (StorageError, ValueError, OSError):
        return ArtifactLocation(sink=sink.name, key=key, code=EXCHANGE_PUBLISH_FAILED)


def _publish_one(
    path: Path,
    staging: RunStaging,
    settings: ExchangeSettings,
    backends: SinkBackends,
    context: PublishContext,
) -> tuple[PublishedArtifact, bool]:
    name = path.relative_to(staging.output_dir).as_posix()
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    sha256 = digest_file(path)
    data = path.read_bytes()
    metadata = {
        "sha256": sha256,
        "run": staging.run_id,
        "namespace": context.namespace,
        "tool": context.tool,
    }
    date = datetime.fromtimestamp(staging.started, UTC).strftime("%Y-%m-%d")
    locations, ok = [], True
    for sink in settings.sinks:
        key = render_key(
            sink,
            namespace=context.namespace,
            tool=context.tool,
            run=staging.run_id,
            name=name,
            date=date,
        )
        location = _write(sink, backends, key, (data, media_type, metadata))
        ok = ok and not (sink.required and location.code)
        locations.append(location)
    artifact = PublishedArtifact(
        name=name,
        sha256=sha256,
        size=len(data),
        media_type=media_type,
        locations=tuple(locations),
    )
    return artifact, ok


def _skipped(path: Path, staging: RunStaging, code: str) -> PublishedArtifact:
    return PublishedArtifact(
        name=path.relative_to(staging.output_dir).as_posix(),
        sha256="",
        size=path.stat().st_size,
        media_type="application/octet-stream",
        code=code,
    )


def publish_outputs(
    staging: RunStaging,
    settings: ExchangeSettings,
    backends: SinkBackends,
    context: PublishContext,
) -> PublishReport:
    """Write every output of ``staging`` to every sink and report the result."""
    report = PublishReport(run_id=staging.run_id)
    for index, path in enumerate(staging.outputs()):
        if index >= settings.max_outputs:
            report.artifacts.append(_skipped(path, staging, EXCHANGE_OUTPUT_LIMIT))
            continue
        if path.stat().st_size > settings.max_output_bytes:
            report.artifacts.append(_skipped(path, staging, EXCHANGE_OUTPUT_TOO_LARGE))
            continue
        artifact, ok = _publish_one(path, staging, settings, backends, context)
        report.artifacts.append(artifact)
        report.ok = report.ok and ok
    return report
