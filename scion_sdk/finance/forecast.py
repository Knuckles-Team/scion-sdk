"""Probabilistic series forecast provider contract (SCION-FINANCE-SOURCES-R901).

The SDK validates the controls, calls the provider, and checks the answer
before any caller sees it: one entry per requested series id, in request order,
echoing the controls, seed and model revision. Model execution stays in the
provider service; nothing here writes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.finance.errors import FinanceProviderError

__all__ = [
    "ForecastError",
    "ForecastRefusal",
    "ForecastRequest",
    "ForecastResponse",
    "request_forecast",
]

_MAX_SEED = 2**64 - 1


class ForecastRefusal(StrEnum):
    CONTROL_OUT_OF_RANGE = "FORECAST_CONTROL_OUT_OF_RANGE"
    SERIES_MISSING = "FORECAST_SERIES_MISSING"
    PROVIDER_UNAVAILABLE = "FORECAST_PROVIDER_UNAVAILABLE"
    MODEL_STALE = "FORECAST_MODEL_STALE"


class ForecastError(FinanceProviderError):
    """A forecast was refused; ``code`` is one closed :class:`ForecastRefusal`."""

    def __init__(self, code: ForecastRefusal) -> None:
        self.code = code
        super().__init__(code.value)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ForecastRequest(_Frozen):
    series_ids: tuple[str, ...] = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    temperature: float
    top_p: float
    sample_count: int
    seed: int


class ForecastResponse(_Frozen):
    per_series: tuple[tuple[str, Any], ...]
    model_revision: str
    seed: int
    temperature: float | None = None
    top_p: float | None = None
    sample_count: int | None = None


Provider = Callable[[ForecastRequest], Awaitable[ForecastResponse]]


def _check_controls(request: ForecastRequest) -> None:
    in_range = (
        0 < request.temperature <= 2
        and 0 < request.top_p <= 1
        and 1 <= request.sample_count <= 1024
        and 0 <= request.seed <= _MAX_SEED
    )
    if not in_range:
        raise ForecastError(ForecastRefusal.CONTROL_OUT_OF_RANGE)


async def request_forecast(
    provider: Provider, request: ForecastRequest
) -> ForecastResponse:
    """Validate ``request``, call ``provider``, and verify its answer."""
    _check_controls(request)
    try:
        response = await provider(request)
    except ForecastError:
        raise
    except Exception:
        raise ForecastError(ForecastRefusal.PROVIDER_UNAVAILABLE) from None
    if response.model_revision != request.model_revision:
        raise ForecastError(ForecastRefusal.MODEL_STALE)
    if tuple(key for key, _ in response.per_series) != request.series_ids:
        raise ForecastError(ForecastRefusal.SERIES_MISSING)
    echoed = (response.temperature, response.top_p, response.sample_count)
    if response.seed != request.seed or echoed != (
        request.temperature,
        request.top_p,
        request.sample_count,
    ):
        raise ForecastError(ForecastRefusal.CONTROL_OUT_OF_RANGE)
    return response
