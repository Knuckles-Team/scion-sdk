"""SCION-FINANCE-SOURCES-R904: the typed finance compute facade."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from scion_sdk.compute.engine import EngineCompute, MethodNotServedError
from scion_sdk.compute.finance import (
    PATTERN_KINDS,
    AmlAlert,
    AmlScreenRequest,
    AmlScreenResult,
    AttributionBreakdown,
    CapitalRatiosRequest,
    CapitalRatiosResult,
    DistributionShiftRequest,
    DistributionShiftResult,
    ExpectedLossRequest,
    GroupEffect,
    PatternClassifyRequest,
    PatternKind,
    PatternMatch,
    PositionSizeRequest,
    ProfitAttributionGroup,
    ProfitAttributionRequest,
    SettlementChainRequest,
    SettlementLeg,
    aml_screen,
    capital_ratios,
    distribution_shift,
    expected_loss,
    pattern_classify,
    position_size,
    profit_attribution,
    settlement_chain,
)

pytestmark = pytest.mark.spec("SCION-FINANCE-SOURCES-R904")

#: The medulla wire results each served method returns (R900.3, R901.3,
#: R750.754).
RESULTS: dict[str, Any] = {
    "finance.position_size": 0.017,
    "finance.distribution_shift": {
        "statistic": 0.21,
        "p_value": 0.004,
        "shifted": True,
    },
    "finance.aml_screen": {
        "alert": {
            "rule_id": "aml-structuring-1",
            "rule_version": 3,
            "kind": "structuring",
            "severity": "warning",
            "amount_minor": 9400,
        }
    },
    "finance.capital_ratios": {
        "cet1_ratio": 0.121,
        "tier1_ratio": 0.152,
        "total_capital_ratio": 0.178,
        "leverage_ratio": 0.204,
        "cet1_meets_minimum": True,
        "total_capital_meets_minimum": True,
        "leverage_meets_minimum": True,
    },
    "finance.expected_loss": 12.5,
    "finance.settlement_chain": [
        {"sequence": 0, "from": "origin-bank", "to": "corr-a", "amount_minor": 10000},
        {
            "sequence": 1,
            "from": "corr-a",
            "to": "beneficiary-bank",
            "amount_minor": 9900,
        },
    ],
    "finance.pattern_classify": [
        {"index": 3, "kind": "hammer", "strength": 0.8},
        {"index": 7, "kind": "inside_bar", "strength": 0.5},
    ],
    "finance.profit_attribution": {
        "groups": [
            {
                "group": "tech",
                "allocation": 0.010,
                "selection": 0.020,
                "interaction": 0.001,
            }
        ],
        "allocation": 0.010,
        "selection": 0.020,
        "interaction": 0.001,
        "active_return": 0.031,
    },
}


class _Finance:
    """The fake engine's finance namespace; records every send it serves."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, dict[str, Any]]] = []

    def _serve(self, name: str, **params: Any) -> Any:
        self.sends.append((name, params))
        return RESULTS[name]

    def position_size(self, **params: Any) -> Any:
        return self._serve("finance.position_size", **params)

    def distribution_shift(self, **params: Any) -> Any:
        return self._serve("finance.distribution_shift", **params)

    def aml_screen(self, **params: Any) -> Any:
        return self._serve("finance.aml_screen", **params)

    def capital_ratios(self, **params: Any) -> Any:
        return self._serve("finance.capital_ratios", **params)

    def expected_loss(self, **params: Any) -> Any:
        return self._serve("finance.expected_loss", **params)

    def settlement_chain(self, **params: Any) -> Any:
        return self._serve("finance.settlement_chain", **params)

    def pattern_classify(self, **params: Any) -> Any:
        return self._serve("finance.pattern_classify", **params)

    def profit_attribution(self, **params: Any) -> Any:
        return self._serve("finance.profit_attribution", **params)


class _Client:
    def __init__(self, finance: _Finance) -> None:
        self.finance = finance


class _NoServeFinance:
    """A finance namespace that serves no method at all."""

    def __init__(self) -> None:
        self.sends: list[tuple[str, dict[str, Any]]] = []


def test_each_function_sends_the_named_method() -> None:
    """Every function sends exactly its named method with the wire field
    names and returns the decoded result model."""
    finance = _Finance()
    compute = EngineCompute(_Client(finance))

    assert (
        position_size(
            compute,
            PositionSizeRequest(
                accuracy=0.6,
                win_loss_ratio=2.0,
                fraction=0.5,
                max_risk=0.02,
                series_revision="rev-1",
            ),
        )
        == 0.017
    )

    shift = distribution_shift(
        compute,
        DistributionShiftRequest(
            reference=[1.0, 2.0], current=[1.5, 2.5], threshold=0.1
        ),
    )
    assert shift == DistributionShiftResult(statistic=0.21, p_value=0.004, shifted=True)

    screen = aml_screen(
        compute,
        AmlScreenRequest(
            rule_id="aml-structuring-1",
            version=3,
            threshold_minor=9000,
            amount_minor=9400,
        ),
    )
    assert screen == AmlScreenResult(
        alert=AmlAlert(
            rule_id="aml-structuring-1",
            rule_version=3,
            kind="structuring",
            severity="warning",
            amount_minor=9400,
        )
    )

    ratios = capital_ratios(
        compute,
        CapitalRatiosRequest(
            cet1=120, at1=150, tier2=28, risk_weighted_assets=990, total_exposure=1200
        ),
    )
    assert ratios == CapitalRatiosResult(
        cet1_ratio=0.121,
        tier1_ratio=0.152,
        total_capital_ratio=0.178,
        leverage_ratio=0.204,
        cet1_meets_minimum=True,
        total_capital_meets_minimum=True,
        leverage_meets_minimum=True,
    )

    assert (
        expected_loss(
            compute, ExpectedLossRequest(pd=0.02, lgd=0.45, ead_minor=250_000)
        )
        == 12.5
    )

    legs = settlement_chain(
        compute,
        SettlementChainRequest(
            origin="origin-bank",
            correspondents=["corr-a"],
            beneficiary="beneficiary-bank",
            amount_minor=10000,
        ),
    )
    assert legs == [
        SettlementLeg(sequence=0, from_="origin-bank", to="corr-a", amount_minor=10000),
        SettlementLeg(
            sequence=1,
            from_="corr-a",
            to="beneficiary-bank",
            amount_minor=9900,
        ),
    ]

    matches = pattern_classify(
        compute,
        PatternClassifyRequest(
            open=[1.0, 1.1], high=[1.2, 1.3], low=[0.9, 1.0], close=[1.15, 1.25]
        ),
    )
    assert matches == [
        PatternMatch(index=3, kind="hammer", strength=0.8),
        PatternMatch(index=7, kind="inside_bar", strength=0.5),
    ]
    kinds: tuple[PatternKind, ...] = tuple(match.kind for match in matches)
    assert kinds == ("hammer", "inside_bar")
    assert "hammer" in PATTERN_KINDS

    attribution = profit_attribution(
        compute,
        ProfitAttributionRequest(
            groups=[
                ProfitAttributionGroup(
                    group="tech", w_p=0.6, w_b=0.5, r_p=0.12, r_b=0.09
                )
            ]
        ),
    )
    assert attribution == AttributionBreakdown(
        groups=[
            GroupEffect(
                group="tech",
                allocation=0.010,
                selection=0.020,
                interaction=0.001,
            )
        ],
        allocation=0.010,
        selection=0.020,
        interaction=0.001,
        active_return=0.031,
    )

    assert finance.sends == [
        (
            "finance.position_size",
            {
                "accuracy": 0.6,
                "win_loss_ratio": 2.0,
                "fraction": 0.5,
                "max_risk": 0.02,
                "series_revision": "rev-1",
            },
        ),
        (
            "finance.distribution_shift",
            {
                "reference": [1.0, 2.0],
                "current": [1.5, 2.5],
                "threshold": 0.1,
            },
        ),
        (
            "finance.aml_screen",
            {
                "rule_id": "aml-structuring-1",
                "version": 3,
                "threshold_minor": 9000,
                "amount_minor": 9400,
            },
        ),
        (
            "finance.capital_ratios",
            {
                "cet1": 120,
                "at1": 150,
                "tier2": 28,
                "risk_weighted_assets": 990,
                "total_exposure": 1200,
            },
        ),
        (
            "finance.expected_loss",
            {"pd": 0.02, "lgd": 0.45, "ead_minor": 250_000},
        ),
        (
            "finance.settlement_chain",
            {
                "origin": "origin-bank",
                "correspondents": ["corr-a"],
                "beneficiary": "beneficiary-bank",
                "amount_minor": 10000,
            },
        ),
        (
            "finance.pattern_classify",
            {
                "open": [1.0, 1.1],
                "high": [1.2, 1.3],
                "low": [0.9, 1.0],
                "close": [1.15, 1.25],
            },
        ),
        (
            "finance.profit_attribution",
            {
                "groups": [
                    {"group": "tech", "w_p": 0.6, "w_b": 0.5, "r_p": 0.12, "r_b": 0.09}
                ]
            },
        ),
    ]


def test_optional_position_size_fields_are_omitted_when_unset() -> None:
    """Only the fields the caller set cross the wire; the engine applies
    its own defaults."""
    finance = _Finance()
    compute = EngineCompute(_Client(finance))

    position_size(compute, PositionSizeRequest(accuracy=0.6, win_loss_ratio=2.0))

    assert finance.sends == [
        ("finance.position_size", {"accuracy": 0.6, "win_loss_ratio": 2.0})
    ]


def test_non_finite_inputs_refuse_before_any_send() -> None:
    """A non-finite input is refused at validation; the engine never sees
    it."""
    finance = _Finance()

    with pytest.raises(ValidationError, match="non-finite"):
        PositionSizeRequest(accuracy=float("inf"), win_loss_ratio=2.0)
    with pytest.raises(ValidationError, match="non-finite"):
        ExpectedLossRequest(pd=float("nan"), lgd=0.45, ead_minor=1)
    with pytest.raises(ValidationError, match="non-finite"):
        PatternClassifyRequest(open=[1.0], high=[float("nan")], low=[0.9], close=[1.1])
    with pytest.raises(ValidationError, match="non-finite"):
        ProfitAttributionRequest(
            groups=[
                ProfitAttributionGroup(
                    group="tech",
                    w_p=float("inf"),
                    w_b=0.5,
                    r_p=0.12,
                    r_b=0.09,
                )
            ]
        )
    with pytest.raises(ValidationError, match="non-finite"):
        DistributionShiftRequest(
            reference=[1.0], current=[1.5], threshold=float("-inf")
        )

    assert finance.sends == []


def test_unserved_methods_refuse_without_computing() -> None:
    """A method the engine does not serve is refused, never computed
    locally, and no call is made."""
    finance = _NoServeFinance()
    compute = EngineCompute(_Client(finance))
    for call in (
        lambda: position_size(
            compute, PositionSizeRequest(accuracy=0.6, win_loss_ratio=2.0)
        ),
        lambda: expected_loss(
            compute, ExpectedLossRequest(pd=0.02, lgd=0.45, ead_minor=1)
        ),
        lambda: profit_attribution(
            compute,
            ProfitAttributionRequest(
                groups=[
                    ProfitAttributionGroup(
                        group="tech", w_p=0.6, w_b=0.5, r_p=0.12, r_b=0.09
                    )
                ]
            ),
        ),
    ):
        with pytest.raises(MethodNotServedError):
            call()

    assert finance.sends == []


def test_closed_result_sets_refuse_unknown_values() -> None:
    """Pattern kinds outside the medulla closed set and out-of-range wire
    values refuse at decode."""
    with pytest.raises(ValidationError):
        PatternMatch(index=0, kind="bogus", strength=0.5)
    with pytest.raises(ValidationError):
        SettlementLeg.model_validate(
            {"sequence": -1, "from": "a", "to": "b", "amount_minor": 1}
        )
