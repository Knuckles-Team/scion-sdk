"""Input resolution from sinks and URLs (SCION-CENTRALIZED-CAPABILITIES-R043.3).

An input is ``exchange://<sink>/<key>`` or an ``https`` URL; plain ``http``
is accepted for loopback only. The file lands in the run's input directory
under its sanitized base name, and the caller passes the absolute staged path
to the tool. Any other value passes through unchanged.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

import httpx

from scion_sdk.http.client import create_http_client
from scion_sdk.http.egress_validation import validate_base_url_resolved
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.storage.base import StorageError, TooLargeError

if TYPE_CHECKING:
    from scion_sdk.exchange.config import ExchangeSettings
    from scion_sdk.exchange.sinks import SinkBackends
    from scion_sdk.exchange.staging import RunStaging

__all__ = [
    "EXCHANGE_EGRESS_REFUSED",
    "EXCHANGE_INPUT_FAILED",
    "EXCHANGE_INPUT_TOO_LARGE",
    "EXCHANGE_SINK_UNKNOWN",
    "EXCHANGE_URL_INPUT_DISABLED",
    "ExchangeInputError",
    "is_input_reference",
    "resolve_input",
]

EXCHANGE_SINK_UNKNOWN = "EXCHANGE_SINK_UNKNOWN"
EXCHANGE_URL_INPUT_DISABLED = "EXCHANGE_URL_INPUT_DISABLED"
EXCHANGE_EGRESS_REFUSED = "EXCHANGE_EGRESS_REFUSED"
EXCHANGE_INPUT_TOO_LARGE = "EXCHANGE_INPUT_TOO_LARGE"
EXCHANGE_INPUT_FAILED = "EXCHANGE_INPUT_FAILED"

_SCHEME = "exchange://"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_CHUNK = 64 * 1024


class ExchangeInputError(ValueError):
    """An input refuses before the tool runs; ``code`` names the refusal."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def is_input_reference(value: object) -> bool:
    """Whether ``value`` names an exchange input the resolver stages."""
    return isinstance(value, str) and value.startswith((_SCHEME, "https://", "http://"))


def _safe_name(raw: str) -> str:
    name = _SAFE_NAME.sub("_", unquote(raw.rsplit("/", 1)[-1])).strip("._")
    return name[:128] or "input"


def _target(staging: RunStaging, raw_name: str) -> Path:
    name = _safe_name(raw_name)
    target, index = staging.input_dir / name, 1
    while target.exists():
        stem, dot, suffix = name.partition(".")
        target = staging.input_dir / f"{stem}-{index}{dot}{suffix}"
        index += 1
    return target


def _from_sink(
    value: str, staging: RunStaging, settings: ExchangeSettings, backends: SinkBackends
) -> Path:
    name, _, key = value[len(_SCHEME) :].partition("/")
    sink = settings.sink(name)
    if sink is None or not key:
        raise ExchangeInputError(EXCHANGE_SINK_UNKNOWN, f"no sink {name!r}")
    try:
        data = backends.backend(sink.store).get_object(
            sink.bucket, key, max_bytes=settings.max_input_bytes
        )
    except TooLargeError:
        raise ExchangeInputError(
            EXCHANGE_INPUT_TOO_LARGE, "input is too large"
        ) from None
    except StorageError as exc:
        raise ExchangeInputError(EXCHANGE_INPUT_FAILED, exc.code) from None
    target = _target(staging, key)
    target.write_bytes(data)
    return target


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _download(
    url: str, target: Path, limit: int, transport: httpx.BaseTransport | None
) -> None:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}/"
    loopback = _loopback((parts.hostname or "").lower())
    options = HttpClientOptions(
        base_url=origin,
        pin_egress=transport is None,
        allow_loopback=loopback and transport is None,
    )
    written = 0
    with (
        create_http_client(options, transport=transport) as client,
        client.stream("GET", url) as response,
    ):
        if response.status_code >= 400:
            raise ExchangeInputError(
                EXCHANGE_INPUT_FAILED, f"status {response.status_code}"
            )
        with target.open("wb") as handle:
            for chunk in response.iter_bytes(_CHUNK):
                written += len(chunk)
                if written > limit:
                    raise ExchangeInputError(
                        EXCHANGE_INPUT_TOO_LARGE, "input is too large"
                    )
                handle.write(chunk)


def _from_url(
    value: str,
    staging: RunStaging,
    settings: ExchangeSettings,
    transport: httpx.BaseTransport | None,
) -> Path:
    if not settings.url_inputs:
        raise ExchangeInputError(EXCHANGE_URL_INPUT_DISABLED, "URL inputs are off")
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    loopback = _loopback(host)
    if parts.username or parts.password or (parts.scheme == "http" and not loopback):
        raise ExchangeInputError(
            EXCHANGE_EGRESS_REFUSED, "URL is outside the egress policy"
        )
    if not validate_base_url_resolved(value, allow_loopback=loopback).allowed:
        raise ExchangeInputError(
            EXCHANGE_EGRESS_REFUSED, "URL is outside the egress policy"
        )
    target = _target(staging, parts.path or "download")
    try:
        _download(value, target, settings.max_input_bytes, transport)
    except ExchangeInputError:
        target.unlink(missing_ok=True)
        raise
    except (httpx.HTTPError, OSError):
        target.unlink(missing_ok=True)
        raise ExchangeInputError(EXCHANGE_INPUT_FAILED, "download failed") from None
    return target


def resolve_input(
    value: object,
    staging: RunStaging,
    settings: ExchangeSettings,
    backends: SinkBackends,
    *,
    transport: httpx.BaseTransport | None = None,
) -> Path | None:
    """Stage one input; ``None`` when ``value`` is not an input reference.

    ``transport`` replaces the network for URL downloads in tests.

    Raises:
        ExchangeInputError: with one of the R043.3 refusal codes.
    """
    if not is_input_reference(value):
        return None
    text = str(value)
    if text.startswith(_SCHEME):
        return _from_sink(text, staging, settings, backends)
    return _from_url(text, staging, settings, transport)
