"""Artifact exchange for tools that produce files (SCION-CENTRALIZED-CAPABILITIES-R043).

A tool run takes inputs from configured sinks or URLs and delivers its output
files to every configured sink. The exchange owns per-run staging, input
resolution, output publication, result links, size limits, retention and
lineage records. Load the settings with :func:`read_exchange_settings` and
wrap a call with :func:`exchange_call`. A connector must not ship its own
staging, upload or link code.
"""

from scion_sdk.exchange.config import (
    EXCHANGE_CONFIG_INVALID,
    EXCHANGE_TEMPLATE_INVALID,
    ExchangeConfigError,
    ExchangeSettings,
    SinkDecl,
    read_exchange_settings,
    render_key,
)
from scion_sdk.exchange.inputs import (
    EXCHANGE_EGRESS_REFUSED,
    EXCHANGE_INPUT_FAILED,
    EXCHANGE_INPUT_TOO_LARGE,
    EXCHANGE_SINK_UNKNOWN,
    EXCHANGE_URL_INPUT_DISABLED,
    ExchangeInputError,
    resolve_input,
)
from scion_sdk.exchange.lineage import EXCHANGE_LINEAGE_FAILED, record_lineage
from scion_sdk.exchange.publish import (
    EXCHANGE_OUTPUT_LIMIT,
    EXCHANGE_OUTPUT_TOO_LARGE,
    EXCHANGE_PUBLISH_FAILED,
    ArtifactLocation,
    PublishContext,
    PublishedArtifact,
    PublishReport,
    publish_outputs,
)
from scion_sdk.exchange.results import ARTIFACTS_META_KEY, attach_artifacts
from scion_sdk.exchange.run import ExchangeCall, exchange_call
from scion_sdk.exchange.sinks import SinkBackends
from scion_sdk.exchange.staging import (
    EXCHANGE_RUN_ID_INVALID,
    RUN_META_KEY,
    ExchangeRunError,
    RunStaging,
    sweep_expired,
)

__all__ = [
    "ARTIFACTS_META_KEY",
    "EXCHANGE_CONFIG_INVALID",
    "EXCHANGE_EGRESS_REFUSED",
    "EXCHANGE_INPUT_FAILED",
    "EXCHANGE_INPUT_TOO_LARGE",
    "EXCHANGE_LINEAGE_FAILED",
    "EXCHANGE_OUTPUT_LIMIT",
    "EXCHANGE_OUTPUT_TOO_LARGE",
    "EXCHANGE_PUBLISH_FAILED",
    "EXCHANGE_RUN_ID_INVALID",
    "EXCHANGE_SINK_UNKNOWN",
    "EXCHANGE_TEMPLATE_INVALID",
    "EXCHANGE_URL_INPUT_DISABLED",
    "RUN_META_KEY",
    "ArtifactLocation",
    "ExchangeCall",
    "ExchangeConfigError",
    "ExchangeInputError",
    "ExchangeRunError",
    "ExchangeSettings",
    "PublishContext",
    "PublishReport",
    "PublishedArtifact",
    "RunStaging",
    "SinkBackends",
    "SinkDecl",
    "attach_artifacts",
    "exchange_call",
    "publish_outputs",
    "read_exchange_settings",
    "record_lineage",
    "render_key",
    "resolve_input",
    "sweep_expired",
]
