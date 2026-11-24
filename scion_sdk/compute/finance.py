"""The typed finance compute facade (SCION-FINANCE-SOURCES-R904).

Each function validates its request model, then sends exactly one medulla
compute method through :class:`~scion_sdk.compute.engine.EngineCompute` on a
session scoped ``compute:finance`` (the default of
:func:`~scion_sdk.compute.engine.connect_compute`). The connector never
computes: a method the engine does not serve raises
:class:`~scion_sdk.compute.engine.MethodNotServedError` and engine refusals
pass through unchanged. The request and result models mirror the medulla
wire fields (MEDULLA-FINANCE-PRIMITIVES-R900.3, R901.3, R750.754); a
non-finite input is refused before any send.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scion_sdk.compute.engine import EngineCompute

__all__ = [
    "PATTERN_KINDS",
    "AmlAlert",
    "AmlScreenRequest",
    "AmlScreenResult",
    "AttributionBreakdown",
    "CapitalRatiosRequest",
    "CapitalRatiosResult",
    "DistributionShiftRequest",
    "DistributionShiftResult",
    "ExpectedLossRequest",
    "GroupEffect",
    "PatternClassifyRequest",
    "PatternKind",
    "PatternMatch",
    "PositionSizeRequest",
    "ProfitAttributionGroup",
    "ProfitAttributionRequest",
    "SettlementChainRequest",
    "SettlementLeg",
    "aml_screen",
    "capital_ratios",
    "distribution_shift",
    "expected_loss",
    "pattern_classify",
    "position_size",
    "profit_attribution",
    "settlement_chain",
]

PatternKind = Literal[
    "hammer",
    "shooting_star",
    "bullish_engulfing",
    "bearish_engulfing",
    "doji",
    "inside_bar",
]
PATTERN_KINDS: tuple[PatternKind, ...] = (
    "hammer",
    "shooting_star",
    "bullish_engulfing",
    "bearish_engulfing",
    "doji",
    "inside_bar",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class _Request(_Model):
    """A request model: every float the wire receives must be finite."""

    @model_validator(mode="after")
    def _require_finite(self) -> Self:
        for name, value in self:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, float) and not math.isfinite(item):
                        raise ValueError(f"{name}: non-finite value")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name}: non-finite value")
        return self


class PositionSizeRequest(_Request):
    """Fractional-Kelly sizing inputs (MEDULLA-FINANCE-PRIMITIVES-R900.3)."""

    accuracy: float
    win_loss_ratio: float
    fraction: float | None = None
    max_risk: float | None = None
    series_revision: str | None = None


class DistributionShiftRequest(_Request):
    """Two-sample Kolmogorov-Smirnov shift inputs (R900.3)."""

    reference: list[float]
    current: list[float]
    threshold: float


class AmlScreenRequest(_Request):
    """One AML screen input in minor units (R901.3)."""

    rule_id: str = Field(min_length=1)
    version: int = Field(ge=0)
    threshold_minor: int
    amount_minor: int


class CapitalRatiosRequest(_Request):
    """Basel-style capital and exposure inputs in minor units (R901.3)."""

    cet1: int
    at1: int
    tier2: int
    risk_weighted_assets: int
    total_exposure: int


class ExpectedLossRequest(_Request):
    """Expected-loss inputs: probability, loss given default, EAD (R901.3)."""

    pd: float
    lgd: float
    ead_minor: int


class SettlementChainRequest(_Request):
    """One correspondent settlement chain in minor units (R901.3)."""

    origin: str = Field(min_length=1)
    correspondents: list[str]
    beneficiary: str = Field(min_length=1)
    amount_minor: int


class PatternClassifyRequest(_Request):
    """Parallel OHLC bars over which the engine classifies patterns
    (R750.754)."""

    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]


class ProfitAttributionGroup(_Request):
    """One group's portfolio and benchmark weight and return (R750.754)."""

    group: str = Field(min_length=1)
    w_p: float
    w_b: float
    r_p: float
    r_b: float


class ProfitAttributionRequest(_Request):
    """Brinson-Fachler attribution inputs (R750.754)."""

    groups: list[ProfitAttributionGroup]


class DistributionShiftResult(_Model):
    """The two-sample shift test result the engine serves."""

    statistic: float
    p_value: float
    shifted: bool


class AmlAlert(_Model):
    """One raised AML alert; ``None`` means the screen passed."""

    rule_id: str
    rule_version: int
    kind: str
    severity: str
    amount_minor: int


class AmlScreenResult(_Model):
    """An AML screen: the alert, or none when the amount is below the band."""

    alert: AmlAlert | None


class CapitalRatiosResult(_Model):
    """Basel-style capital ratios and their minimum checks."""

    cet1_ratio: float
    tier1_ratio: float
    total_capital_ratio: float
    leverage_ratio: float
    cet1_meets_minimum: bool
    total_capital_meets_minimum: bool
    leverage_meets_minimum: bool


class SettlementLeg(_Model):
    """One leg of a correspondent settlement chain."""

    sequence: int = Field(ge=0)
    from_: str = Field(alias="from")
    to: str
    amount_minor: int


class PatternMatch(_Model):
    """One price-action pattern found at a bar index."""

    index: int = Field(ge=0)
    kind: PatternKind
    strength: float


class GroupEffect(_Model):
    """One group's allocation, selection and interaction effect."""

    group: str
    allocation: float
    selection: float
    interaction: float


class AttributionBreakdown(_Model):
    """Brinson-Fachler attribution: per-group effects and totals."""

    groups: list[GroupEffect]
    allocation: float
    selection: float
    interaction: float
    active_return: float


def position_size(compute: EngineCompute, /, request: PositionSizeRequest) -> float:
    """The fractional-Kelly stake the engine serves for one position."""
    params: dict[str, Any] = {
        "accuracy": request.accuracy,
        "win_loss_ratio": request.win_loss_ratio,
    }
    if request.fraction is not None:
        params["fraction"] = request.fraction
    if request.max_risk is not None:
        params["max_risk"] = request.max_risk
    if request.series_revision is not None:
        params["series_revision"] = request.series_revision
    return float(compute.call("finance.position_size", **params))


def distribution_shift(
    compute: EngineCompute, /, request: DistributionShiftRequest
) -> DistributionShiftResult:
    """The two-sample shift test between reference and current samples."""
    result = compute.call(
        "finance.distribution_shift",
        reference=request.reference,
        current=request.current,
        threshold=request.threshold,
    )
    return DistributionShiftResult.model_validate(result)


def aml_screen(compute: EngineCompute, /, request: AmlScreenRequest) -> AmlScreenResult:
    """Screen one amount against one AML rule version."""
    result = compute.call(
        "finance.aml_screen",
        rule_id=request.rule_id,
        version=request.version,
        threshold_minor=request.threshold_minor,
        amount_minor=request.amount_minor,
    )
    return AmlScreenResult.model_validate(result)


def capital_ratios(
    compute: EngineCompute, /, request: CapitalRatiosRequest
) -> CapitalRatiosResult:
    """The capital ratios and minimum checks for one institution."""
    result = compute.call(
        "finance.capital_ratios",
        cet1=request.cet1,
        at1=request.at1,
        tier2=request.tier2,
        risk_weighted_assets=request.risk_weighted_assets,
        total_exposure=request.total_exposure,
    )
    return CapitalRatiosResult.model_validate(result)


def expected_loss(compute: EngineCompute, /, request: ExpectedLossRequest) -> float:
    """The expected loss the engine serves for one exposure."""
    return float(
        compute.call(
            "finance.expected_loss",
            pd=request.pd,
            lgd=request.lgd,
            ead_minor=request.ead_minor,
        )
    )


def settlement_chain(
    compute: EngineCompute, /, request: SettlementChainRequest
) -> list[SettlementLeg]:
    """The settlement legs the engine plans for one chain."""
    result = compute.call(
        "finance.settlement_chain",
        origin=request.origin,
        correspondents=request.correspondents,
        beneficiary=request.beneficiary,
        amount_minor=request.amount_minor,
    )
    return [SettlementLeg.model_validate(leg) for leg in result]


def pattern_classify(
    compute: EngineCompute, /, request: PatternClassifyRequest
) -> list[PatternMatch]:
    """The price-action patterns the engine classifies over the bars."""
    result = compute.call(
        "finance.pattern_classify",
        open=request.open,
        high=request.high,
        low=request.low,
        close=request.close,
    )
    return [PatternMatch.model_validate(match) for match in result]


def profit_attribution(
    compute: EngineCompute, /, request: ProfitAttributionRequest
) -> AttributionBreakdown:
    """The Brinson-Fachler attribution the engine computes over the groups."""
    result = compute.call(
        "finance.profit_attribution",
        groups=[
            {
                "group": group.group,
                "w_p": group.w_p,
                "w_b": group.w_b,
                "r_p": group.r_p,
                "r_b": group.r_b,
            }
            for group in request.groups
        ],
    )
    return AttributionBreakdown.model_validate(result)
