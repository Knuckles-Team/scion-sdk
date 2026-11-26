"""Errors the connector-sync runner raises."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from scion_sdk.ports.errors import SourceContractError
from scion_sdk.schema_drift import SchemaDriftReport

__all__ = [
    "CredentialResolutionError",
    "EngineSchemaDriftQuarantined",
    "RunnerConfigurationError",
    "SchemaDriftQuarantined",
    "SinkReceiptError",
    "StageCycleFailed",
    "StageRefusal",
    "StreamPaused",
]


class RunnerConfigurationError(ValueError):
    """The runner configuration or a connector package is unusable.

    Messages name fields, never their values.
    """


class CredentialResolutionError(PermissionError):
    """A connector's credential reference could not be resolved; no session opens."""


class SinkReceiptError(RuntimeError):
    """A sink receipt does not acknowledge exactly what was submitted."""


class SchemaDriftQuarantined(SourceContractError):
    """A page needs review; the source retains it behind the durable checkpoint."""

    def __init__(self, report: SchemaDriftReport) -> None:
        self.report = report
        reasons = ",".join(report.reason_codes)
        super().__init__(
            f"source page quarantined: {report.classification.value}; {reasons}"
        )


class EngineSchemaDriftQuarantined(SchemaDriftQuarantined):
    """The engine gate quarantined a page (``SCHEMA_DRIFT_QUARANTINED``).

    The checkpoint is held. ``drift`` is the engine's per-stream report, kept
    unchanged, and ``drift_digest`` is the SHA-256 of its canonical JSON for the
    run record (SCION-SOURCE-INGEST-R007.1). The engine is the authority; the SDK
    classifier never overrides it.
    """

    def __init__(self, drift: list[Any]) -> None:
        self.report = None  # type: ignore[assignment]
        self.drift = drift
        self.drift_digest = _digest(drift)
        SourceContractError.__init__(
            self, f"engine quarantined the page; drift digest {self.drift_digest}"
        )


def _digest(drift: list[Any]) -> str:
    body = [
        item.model_dump(mode="json", by_alias=True)
        if hasattr(item, "model_dump")
        else item
        for item in drift
    ]
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class StreamPaused(SourceContractError):
    """A proposed schema repair is unresolved; the sync loop takes no action.

    Raised before any page is extracted or submitted (SCION-SOURCE-INGEST-R003):
    pausing never constructs a candidate schema, validates instances, performs
    a shadow ingest, or activates a repair.
    """

    def __init__(self, source: str, stream: str) -> None:
        self.source = source
        self.stream = stream
        super().__init__(
            f"stream {stream!r} of {source!r} is paused on an unresolved "
            "repair proposal"
        )


class StageRefusal(RunnerConfigurationError):
    """A stage graph or commit is unsafe; ``code`` is one of the closed set."""

    CODES = frozenset(
        {
            "STAGE_IDENTITY_COLLISION",
            "STAGE_CYCLE",
            "STAGE_LIMITS_ABSENT",
            "STAGE_SCOPE_MISMATCH",
            "STAGE_RECEIPT_MISMATCH",
            "STAGE_PROGRESS_INHERITED",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self.CODES:
            raise ValueError(f"unknown stage refusal code: {code}")
        self.code = code
        super().__init__(code)


class StageCycleFailed(RuntimeError):
    """A cycle's stages did not all complete; the report holds the detail."""

    def __init__(self, connector: str) -> None:
        self.connector = connector
        super().__init__(f"connector {connector!r}: not every stage completed")
