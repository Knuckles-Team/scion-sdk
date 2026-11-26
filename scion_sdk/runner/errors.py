"""Errors the connector-sync runner raises."""

from __future__ import annotations

from scion_sdk.ports.errors import SourceContractError
from scion_sdk.schema_drift import SchemaDriftReport

__all__ = [
    "CredentialResolutionError",
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
