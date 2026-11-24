"""SCION-FINANCE-SOURCES-R902: quant analyses route to NumericEvaluate.

The seam owner is MEDULLA-FINANCE-PRIMITIVES-R002.2.5: the engine renders a
kernel refusal as ``INVALID_ARGUMENT`` with the stable ``KERNEL_*`` code
first in the detail, and a retired ``Finance<Kernel>`` method name fails to
decode. The in-process engine below stands in for the served engine; the
vectors mirror the shared ``numeric_evaluate_vectors.json`` fixture.
"""

from __future__ import annotations

from typing import Any

import pytest
from medulla.client import EngineResponseError

from scion_sdk.compute import (
    AlgorithmBinding,
    ComputeRefusal,
    EngineCompute,
    MethodNotServedError,
)

pytestmark = pytest.mark.spec("SCION-FINANCE-SOURCES-R902")

VOCAB = "https://knuckles-team.github.io/ontology/quant#"
ZSCORE = VOCAB + "RollingZScoreAnalysis"
EWMA = VOCAB + "EwmaAnalysis"

#: The wire fields of two R002.2.5 ops, as the connector sends them.
ZSCORE_FIELDS: dict[str, Any] = {
    "values": [-1.1, -0.4, 0.3, 1.0, -0.6, 0.1, 0.8, -0.8, -0.1, 0.6, -1.0, -0.3],
    "window": 4,
}
EWMA_FIELDS: dict[str, Any] = {
    "values": [-1.1, -0.4, 0.3],
    "smoothing": {"span": 5.0},
}


class _Numeric:
    """The in-process engine's numeric namespace: the served evaluate dispatch."""

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.refuse_with: str | None = None
        self.other_error: Exception | None = None

    def evaluate(self, *, op: str, **fields: Any) -> Any:
        self.sends.append({"op": op, **fields})
        if self.other_error is not None:
            raise self.other_error
        if self.refuse_with is not None:
            raise EngineResponseError(
                "INVALID_ARGUMENT", f"{self.refuse_with}: the input is refused"
            )
        return {"op": op, "result": [0.1, 0.2, 0.3]}


class _Finance:
    """The finance namespace after the kernel retirement: no kernel methods."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def signal_models(self, **params: Any) -> dict[str, Any]:
        self.calls.append("signal_models")
        return {"posterior_up": 0.7}


class _Rdf:
    """The in-process ontology read of the connector's quant pack."""

    def __init__(self, bindings: dict[str, list[dict[str, Any]]]) -> None:
        self.bindings = bindings
        self.queries: list[str] = []

    def sparql(self, *, query: str) -> list[dict[str, Any]]:
        self.queries.append(query)
        for analysis, rows in self.bindings.items():
            if f"<{analysis}>" in query:
                return rows
        return []


class _Client:
    """A conformed connector's client: ``numeric.evaluate`` served, the
    per-kernel ``Finance<Kernel>`` paths retired."""

    def __init__(self) -> None:
        self.numeric = _Numeric()
        self.finance = _Finance()
        self.rdf = _Rdf(
            {
                ZSCORE: [
                    {
                        "algorithm": VOCAB + "RollingZScoreKernel",
                        "method": "numeric.evaluate",
                        "op": "rolling_z_score",
                    }
                ],
                EWMA: [
                    {
                        "algorithm": VOCAB + "EwmaKernel",
                        "method": "numeric.evaluate",
                        "op": "ewma",
                    }
                ],
            }
        )


def test_analyse_routes_to_numeric_evaluate() -> None:
    """Both quant analyses bind one served method, ``numeric.evaluate``, with
    their kernel op; the adapter forwards each op's wire fields unchanged."""
    client = _Client()
    compute = EngineCompute(client)

    first = compute.analyse(ZSCORE, vocabulary=VOCAB, **ZSCORE_FIELDS)
    second = compute.analyse(EWMA, vocabulary=VOCAB, **EWMA_FIELDS)

    assert first == {"op": "rolling_z_score", "result": [0.1, 0.2, 0.3]}
    assert second == {"op": "ewma", "result": [0.1, 0.2, 0.3]}
    assert client.numeric.sends == [
        {"op": "rolling_z_score", **ZSCORE_FIELDS},
        {"op": "ewma", **EWMA_FIELDS},
    ]
    # The selection asked the ontology for the served method and its op.
    query = client.rdf.queries[0]
    assert f"<{VOCAB}servedBy> ?method" in query
    assert f"<{VOCAB}servedOp> ?op" in query
    # Nothing was computed through a retired per-kernel path.
    assert client.finance.calls == []


KERNEL_CODES = (
    "KERNEL_INPUT_SHORT",
    "KERNEL_INPUT_NONFINITE",
    "KERNEL_LENGTH_MISMATCH",
    "KERNEL_PARAM_INVALID",
    "KERNEL_SINGULAR",
    "KERNEL_STATE_BUDGET",
    "KERNEL_WORK_BUDGET",
    "KERNEL_OP_UNKNOWN",
)


@pytest.mark.parametrize("code", KERNEL_CODES)
def test_kernel_refusal_codes_surface_as_compute_refusal(code) -> None:
    """A stable engine kernel code reaches the caller as a ComputeRefusal
    carrying that code; the diagnostic detail stays readable."""
    client = _Client()
    client.numeric.refuse_with = code
    compute = EngineCompute(client)

    with pytest.raises(ComputeRefusal) as caught:
        compute.call("numeric.evaluate", op="adf", series=[0.1], max_lag=1)

    assert caught.value.code == code
    assert caught.value.detail == "the input is refused"
    assert str(caught.value) == f"{code}: the input is refused"
    assert client.numeric.sends == [{"op": "adf", "series": [0.1], "max_lag": 1}]


def test_refusal_surfaces_through_the_analysis_path() -> None:
    """The same refusal reaches a caller that went through the ontology
    selection, not a direct call."""
    client = _Client()
    client.numeric.refuse_with = "KERNEL_LENGTH_MISMATCH"
    compute = EngineCompute(client)

    with pytest.raises(ComputeRefusal) as caught:
        compute.analyse(ZSCORE, vocabulary=VOCAB, **ZSCORE_FIELDS)

    assert caught.value.code == "KERNEL_LENGTH_MISMATCH"


def test_retired_finance_kernel_paths_are_not_served() -> None:
    """The retired per-kernel ``finance.<kernel>`` paths fail closed: they do
    not exist on the client, they do not decode through the adapter, and
    nothing is computed locally."""
    client = _Client()
    compute = EngineCompute(client)

    assert not hasattr(client.finance, "rolling_z_score")

    with pytest.raises(MethodNotServedError) as direct:
        compute.call("finance.rolling_z_score", values=[0.1], window=2)
    assert direct.value.method == "finance.rolling_z_score"

    binding = AlgorithmBinding(VOCAB + "RetiredBinding", "finance.rolling_z_score")
    with pytest.raises(MethodNotServedError) as routed:
        compute.run(binding, values=[0.1], window=2)
    assert routed.value.method == "finance.rolling_z_score"

    assert client.numeric.sends == []
    assert client.finance.calls == []


def test_non_kernel_refusals_pass_through_unchanged() -> None:
    """An engine refusal without a stable kernel code is not reclassified."""
    client = _Client()
    client.numeric.other_error = EngineResponseError(
        "FORBIDDEN", "the caller lacks compute:numeric"
    )
    compute = EngineCompute(client)

    with pytest.raises(EngineResponseError) as caught:
        compute.call("numeric.evaluate", op="brier", forecasts=[0.5], outcomes=[1])

    assert caught.value.code == "FORBIDDEN"
    assert not isinstance(caught.value, ComputeRefusal)


def test_a_kernel_mention_is_not_a_refusal_code() -> None:
    """Kernel text inside a diagnostic is not a stable code: only the code
    first in the refusal is classified."""
    client = _Client()
    client.numeric.other_error = EngineResponseError(
        "INTERNAL", "the op KERNEL_ROLLING_Z is being registered"
    )
    compute = EngineCompute(client)

    with pytest.raises(EngineResponseError) as caught:
        compute.call("numeric.evaluate", op="rolling_z_score", **ZSCORE_FIELDS)

    assert caught.value.code == "INTERNAL"
    assert not isinstance(caught.value, ComputeRefusal)
