"""SCION-CONNECTOR-CONTROL-R904: ranked backend order and outcome reporting."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from test_platform_fallback import request
from test_platform_fallback_served import _EMPTY, _call, _served

from scion_sdk import decide
from scion_sdk.adapters.platform_order import (
    PATH_PREFERENCE_QUESTION,
    ordered_policies,
    path_id,
    path_order_timeout_s,
)
from scion_sdk.decide.medulla import MedullaDecisionRunner, evaluate_op
from scion_sdk.decide.options import Option
from scion_sdk.decide.points import Binding, DecisionPoint, LogMode, StaticBindings
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse

POINT = DecisionPoint(
    PATH_PREFERENCE_QUESTION, "path_preference", log_mode=LogMode.NEVER
)
BINDINGS = StaticBindings(
    {PATH_PREFERENCE_QUESTION: Binding({"component_id": "schema"}, {"policy": "p"})}
)


def _pid(backend: str) -> str:
    return path_id("connector", "demo", "items", "search", backend)


class RecordingTransport:
    """The SDK-side fake of the engine: answers Decide with a fixed advisory."""

    def __init__(self, ranking: list[str] | None) -> None:
        self.ranking = ranking
        self.decisions: list[Any] = []
        self.ops: list[Any] = []

    async def decide(self, request: Any) -> Any:
        self.decisions.append(request)
        if self.ranking is None:
            return {"records": [{"outcome": {"outcome": "abstained", "reasons": []}}]}
        scores = [
            {"option_id": _pid(b), "score": {"value": 100 - i}}
            for i, b in enumerate(self.ranking)
        ]
        return {"records": [{"outcome": {"outcome": "advisory", "scores": scores}}]}

    async def log(self, op: Any) -> Any:
        self.ops.append(op)

    def run(self, call: Any) -> Any:
        raise AssertionError("sync bridge is not used")


class BrokenLog(RecordingTransport):
    async def log(self, op: Any) -> Any:
        raise RuntimeError("engine log unavailable")


def _runner(transport: RecordingTransport) -> MedullaDecisionRunner:
    return MedullaDecisionRunner(
        transport=transport,
        tenant="tenant",
        bindings=BINDINGS,
        points={PATH_PREFERENCE_QUESTION: POINT},
    )


def _order(result: dict[str, Any]) -> list[str]:
    return [a["backend_id"] for a in result["attempts"]]


async def _search(tmp_path, monkeypatch, a_response, b_response):
    with (
        ScriptedHttpServer(a_response) as first,
        ScriptedHttpServer(b_response) as second,
    ):
        server = _served(first, second, tmp_path, monkeypatch)
        result = await _call(server, "platform_search", request())
        return result, len(first.requests), len(second.requests)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R904.1")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R904")
async def test_transport_follows_ranked_path_order(tmp_path, monkeypatch) -> None:
    transport = RecordingTransport(["second", "first"])
    token = decide.use_runner(_runner(transport))
    try:
        result, first_calls, second_calls = await _search(
            tmp_path, monkeypatch, _EMPTY, _EMPTY
        )
    finally:
        decide.use_runner(None)
        del token
    assert result["selected_backend"] == "second"
    assert _order(result) == ["second"]
    assert (first_calls, second_calls) == (0, 1)
    offered = transport.decisions[0]["candidates"]
    assert transport.decisions[0]["question"]["question_id"] == PATH_PREFERENCE_QUESTION
    assert str(offered).count(_pid("first")) == 1 and _pid("second") in str(offered)

    # No runner: declared order.
    result, first_calls, second_calls = await _search(
        tmp_path / "plain", monkeypatch, _EMPTY, _EMPTY
    )
    assert result["selected_backend"] == "first"
    assert (first_calls, second_calls) == (1, 0)

    # An abstention keeps the declared order.
    decide.use_runner(_runner(RecordingTransport(None)))
    try:
        result, _, _ = await _search(tmp_path / "abstain", monkeypatch, _EMPTY, _EMPTY)
    finally:
        decide.use_runner(None)
    assert result["selected_backend"] == "first"


def test_path_id_is_the_published_digest_chain() -> None:
    params = hashlib.sha256(b"demo\0items\0search").hexdigest()
    expected = hashlib.sha256(f"connector\0first\0{params}".encode()).hexdigest()
    assert _pid("first") == expected
    assert len(expected) == 64 and expected == expected.lower()


def test_order_timeout_is_clamped(monkeypatch) -> None:
    monkeypatch.delenv("CONNECTOR_PATH_ORDER_TIMEOUT_MS", raising=False)
    assert path_order_timeout_s() == 0.25
    monkeypatch.setenv("CONNECTOR_PATH_ORDER_TIMEOUT_MS", "1")
    assert path_order_timeout_s() == 0.01
    monkeypatch.setenv("CONNECTOR_PATH_ORDER_TIMEOUT_MS", "999999")
    assert path_order_timeout_s() == 5.0


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R904.2")
async def test_outcomes_report_through_evaluate(tmp_path, monkeypatch) -> None:
    transport = RecordingTransport(["second", "first"])
    decide.use_runner(_runner(transport))
    try:
        result, _, _ = await _search(
            tmp_path, monkeypatch, _EMPTY, ScriptedResponse(status=503)
        )
        assert result["selected_backend"] == "first"
        assert _order(result) == ["second", "first"]
        ops = {o["evaluation"]["selected_agent"]: o for o in transport.ops}
        assert set(ops) == {_pid("first"), _pid("second")}
        assert ops[_pid("second")]["evaluation"]["success"] is False
        assert ops[_pid("first")]["evaluation"]["success"] is True
        rid = request().request_id
        expected = hashlib.sha256(f"{rid}\0second".encode()).hexdigest()
        assert ops[_pid("second")]["evaluation"]["evaluation_id"] == expected

        # A terminal reason says nothing about path health and is not reported.
        transport.ops.clear()
        result, _, _ = await _search(
            tmp_path / "denied",
            monkeypatch,
            _EMPTY,
            ScriptedResponse(status=401),
        )
        assert result["reason"] == "unauthorized"
        assert transport.ops == []
    finally:
        decide.use_runner(None)

    # A failing log never changes or delays the result.
    broken = BrokenLog(["second", "first"])
    decide.use_runner(_runner(broken))
    try:
        result, _, _ = await _search(
            tmp_path / "broken", monkeypatch, _EMPTY, ScriptedResponse(status=503)
        )
    finally:
        decide.use_runner(None)
    assert result["selected_backend"] == "first"


def test_evaluate_op_round_trips_the_engine_type() -> None:
    from medulla.generated.models import DecisionLogOpEvaluate

    op = evaluate_op("tenant", PATH_PREFERENCE_QUESTION, _pid("first"), "ok", "e" * 64)
    parsed = DecisionLogOpEvaluate.model_validate(op)
    assert parsed.model_dump(mode="json", by_alias=True, exclude_none=True) == op


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R904.3")
async def test_served_read_fails_over_in_ranked_order(tmp_path, monkeypatch) -> None:
    transport = RecordingTransport(["second", "first"])
    decide.use_runner(_runner(transport))
    try:
        result, first_calls, second_calls = await _search(
            tmp_path, monkeypatch, _EMPTY, ScriptedResponse(status=503)
        )
    finally:
        decide.use_runner(None)
    assert _order(result) == ["second", "first"]
    assert result["outcome"] == "ok" and (first_calls, second_calls) == (1, 1)
    assert [o["op"] for o in transport.ops] == ["evaluate", "evaluate"]
    # Without a runner the declared order runs.
    result, first_calls, second_calls = await _search(
        tmp_path / "none", monkeypatch, _EMPTY, _EMPTY
    )
    assert _order(result) == ["first"]


def test_option_ids_are_distinct_per_backend() -> None:
    assert Option(_pid("first")).option_id != Option(_pid("second")).option_id


async def test_ordered_policies_ranks_declared_backends() -> None:
    from test_platform_fallback import capability

    cap = capability()
    declared = [b.backend_id for b in cap.backends]
    assert [b.backend_id for b in await ordered_policies("connector", cap)] == declared
    decide.use_runner(_runner(RecordingTransport(list(reversed(declared)))))
    try:
        ranked = await ordered_policies("connector", cap)
    finally:
        decide.use_runner(None)
    assert [b.backend_id for b in ranked] == list(reversed(declared))
