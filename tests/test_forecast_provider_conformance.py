"""Forecast provider contract against a loopback provider (R901)."""

from __future__ import annotations

import pytest

from scion_sdk.finance.forecast import (
    ForecastError,
    ForecastRefusal,
    ForecastRequest,
    ForecastResponse,
    request_forecast,
)


def _request(**changes: object) -> ForecastRequest:
    base: dict[str, object] = {
        "series_ids": ("b", "a"),
        "model_revision": "r1",
        "temperature": 1.0,
        "top_p": 0.9,
        "sample_count": 8,
        "seed": 7,
    }
    return ForecastRequest.model_validate({**base, **changes})


def _answer(request: ForecastRequest, **changes: object) -> ForecastResponse:
    body: dict[str, object] = {
        "per_series": tuple((s, [1.0]) for s in request.series_ids),
        "model_revision": request.model_revision,
        "seed": request.seed,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "sample_count": request.sample_count,
    }
    return ForecastResponse.model_validate({**body, **changes})


@pytest.mark.spec("SCION-FINANCE-SOURCES-R901")
async def test_controls_order_and_refusals() -> None:
    async def loopback(request: ForecastRequest) -> ForecastResponse:
        return _answer(request)

    answer = await request_forecast(loopback, _request())
    assert [key for key, _ in answer.per_series] == ["b", "a"]
    assert (answer.seed, answer.model_revision) == (7, "r1")

    for changes in (
        {"temperature": 0.0},
        {"temperature": 2.5},
        {"top_p": 0.0},
        {"top_p": 1.5},
        {"sample_count": 0},
        {"sample_count": 1025},
        {"seed": -1},
        {"seed": 2**64},
    ):
        with pytest.raises(ForecastError) as caught:
            await request_forecast(loopback, _request(**changes))
        assert caught.value.code is ForecastRefusal.CONTROL_OUT_OF_RANGE

    async def missing(request: ForecastRequest) -> ForecastResponse:
        return _answer(request, per_series=(("b", [1.0]),))

    async def stale(request: ForecastRequest) -> ForecastResponse:
        return _answer(request, model_revision="r0")

    async def down(request: ForecastRequest) -> ForecastResponse:
        raise ConnectionError("planted")

    async def reordered(request: ForecastRequest) -> ForecastResponse:
        return _answer(request, per_series=(("a", [1.0]), ("b", [1.0])))

    expected = (
        (missing, ForecastRefusal.SERIES_MISSING),
        (reordered, ForecastRefusal.SERIES_MISSING),
        (stale, ForecastRefusal.MODEL_STALE),
        (down, ForecastRefusal.PROVIDER_UNAVAILABLE),
    )
    for provider, code in expected:
        with pytest.raises(ForecastError) as caught:
            await request_forecast(provider, _request())
        assert caught.value.code is code and "planted" not in str(caught.value)
