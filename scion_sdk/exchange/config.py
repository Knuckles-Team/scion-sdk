"""Exchange settings and sinks (SCION-CENTRALIZED-CAPABILITIES-R043.1).

``SCION_EXCHANGE_FILE`` names one JSON document with a ``stores`` map (the
store registry of R042.6) and a ``sinks`` list. Scalar knobs come from the
environment with bounded starting values. An unset file turns the exchange
off: :func:`read_exchange_settings` returns ``None``.
"""

from __future__ import annotations

import json
import string
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scion_sdk.config import setting
from scion_sdk.paths import data_dir
from scion_sdk.storage.base import InvalidNameError, validate_key
from scion_sdk.storage.config import StoreConfig, parse_stores

__all__ = [
    "EXCHANGE_CONFIG_INVALID",
    "EXCHANGE_TEMPLATE_INVALID",
    "TEMPLATE_VARIABLES",
    "ExchangeConfigError",
    "ExchangeSettings",
    "SinkDecl",
    "read_exchange_settings",
    "render_key",
]

EXCHANGE_CONFIG_INVALID = "EXCHANGE_CONFIG_INVALID"
EXCHANGE_TEMPLATE_INVALID = "EXCHANGE_TEMPLATE_INVALID"

#: The only variables a sink path template may name.
TEMPLATE_VARIABLES = frozenset({"namespace", "tool", "date", "run", "name"})
_SAMPLE = {
    "namespace": "app",
    "tool": "tool",
    "date": "2026-01-01",
    "run": "run",
    "name": "file.bin",
}


class ExchangeConfigError(ValueError):
    """A settings document refuses at load; ``code`` names the refusal."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class SinkDecl(BaseModel):
    """One output destination: a store, a bucket and a key template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    store: str
    bucket: str
    path_template: str = "{namespace}/{date}/{run}/{name}"
    signed_url_ttl_s: int = Field(default=0, ge=0, le=604_800)
    required: bool = False


class ExchangeSettings(BaseModel):
    """The exchange knobs, stores and sinks of one connector process."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    staging_root: Path
    max_input_bytes: int = 268_435_456
    max_output_bytes: int = 1_073_741_824
    max_outputs: int = 64
    retention_s: int = 86_400
    url_inputs: bool = True
    lineage: bool = False
    stores: dict[str, StoreConfig]
    sinks: tuple[SinkDecl, ...]

    def sink(self, name: str) -> SinkDecl | None:
        """The sink called ``name``, or ``None``."""
        return next((s for s in self.sinks if s.name == name), None)


def _check_template(sink: SinkDecl) -> None:
    fields = {f for _, f, _, _ in string.Formatter().parse(sink.path_template) if f}
    unknown = sorted(fields - TEMPLATE_VARIABLES)
    if unknown or "{name}" not in sink.path_template:
        raise ExchangeConfigError(
            EXCHANGE_TEMPLATE_INVALID,
            f"sink {sink.name!r} template must use only {sorted(TEMPLATE_VARIABLES)} and {{name}}",
        )
    try:
        validate_key(sink.path_template.format(**_SAMPLE))
    except InvalidNameError:
        raise ExchangeConfigError(
            EXCHANGE_TEMPLATE_INVALID, f"sink {sink.name!r} renders an unsafe key"
        ) from None


def render_key(sink: SinkDecl, **values: str) -> str:
    """The object key a sink writes for one output file."""
    defaults = {"date": datetime.now(UTC).strftime("%Y-%m-%d")}
    return validate_key(sink.path_template.format(**{**defaults, **values}))


def _sinks(raw: Any, stores: dict[str, StoreConfig]) -> tuple[SinkDecl, ...]:
    if not isinstance(raw, list) or not raw:
        raise ExchangeConfigError(
            EXCHANGE_CONFIG_INVALID, "sinks must be a non-empty list"
        )
    sinks = tuple(SinkDecl.model_validate(entry) for entry in raw)
    names = [s.name for s in sinks]
    if len(set(names)) != len(names):
        raise ExchangeConfigError(EXCHANGE_CONFIG_INVALID, "sink names repeat")
    for sink in sinks:
        if sink.store not in stores:
            raise ExchangeConfigError(
                EXCHANGE_CONFIG_INVALID, f"sink {sink.name!r} names an unknown store"
            )
        _check_template(sink)
    return sinks


def _document(path: str) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ExchangeConfigError(
            EXCHANGE_CONFIG_INVALID, "SCION_EXCHANGE_FILE is unreadable"
        ) from None
    if not isinstance(document, dict):
        raise ExchangeConfigError(EXCHANGE_CONFIG_INVALID, "the document is an object")
    return document


def read_exchange_settings() -> ExchangeSettings | None:
    """Load the exchange settings; ``None`` when ``SCION_EXCHANGE_FILE`` is unset.

    Raises:
        ExchangeConfigError: ``EXCHANGE_CONFIG_INVALID`` or
            ``EXCHANGE_TEMPLATE_INVALID``.
        StoreConfigError: a store refuses (``STORAGE_CONFIG_INVALID`` or
            ``STORAGE_SECRET_LITERAL``).
    """
    path = setting("SCION_EXCHANGE_FILE", "")
    if not path:
        return None
    document = _document(path)
    stores = parse_stores(document.get("stores") or {})
    try:
        sinks = _sinks(document.get("sinks"), stores)
    except ValidationError as exc:
        raise ExchangeConfigError(
            EXCHANGE_CONFIG_INVALID, f"a sink is malformed ({exc.error_count()} errors)"
        ) from None
    return ExchangeSettings(
        staging_root=Path(
            setting("SCION_EXCHANGE_STAGING_ROOT", str(data_dir() / "exchange"))
        ),
        max_input_bytes=setting("SCION_EXCHANGE_MAX_INPUT_BYTES", 268_435_456),
        max_output_bytes=setting("SCION_EXCHANGE_MAX_OUTPUT_BYTES", 1_073_741_824),
        max_outputs=setting("SCION_EXCHANGE_MAX_OUTPUTS", 64),
        retention_s=setting("SCION_EXCHANGE_RETENTION_S", 86_400),
        url_inputs=setting("SCION_EXCHANGE_URL_INPUTS", True),
        lineage=setting("SCION_EXCHANGE_LINEAGE", False),
        stores=stores,
        sinks=sinks,
    )
