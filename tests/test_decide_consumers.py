"""SCION-CONNECTOR-CONTROL-R006 / SCION-GOVERNED-WRITEBACK-R001: the SDK's own
``Decide`` call sites are evaluate-only and never authorize; all three paths
(no runner installed, EG abstains, EG names something outside the offered
set) fall back deterministically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from scion_sdk import decide
from scion_sdk.decide import Choice, DecisionRunner, Option
from scion_sdk.decide.consumers import (
    NO_WRITE,
    bounded_choice,
    propose_writeback,
    ranked_choice,
)
from scion_sdk.decide.options import (
    Q32_ONE,
    RankCandidate,
    declared_source,
    q32,
    text_param,
    unique_sorted,
)
from scion_sdk.decide.runner import Fallback


@dataclass
class FakeRunner:
    """Answers every ``choose``/``achoose`` with a scripted :class:`Choice`.

    ``answer`` is either a fixed :class:`Choice` or a callable from the
    fallback's own answer to one -- so a scripted "EG abstains" reply can
    still return the correct fallback value without the test hardcoding it.
    """

    answer: Choice | Callable[[Fallback], Choice]
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def _reply(
        self, question_id: str, options: Sequence[Option], fallback: Fallback
    ) -> Choice:
        self.calls.append((question_id, tuple(o.option_id for o in options)))
        if isinstance(self.answer, Choice):
            return self.answer
        return self.answer(fallback)

    def choose(
        self,
        question_id: str,
        options: Sequence[Option],
        fallback: Fallback,
        **kwargs: Any,
    ) -> Choice:
        return self._reply(question_id, options, fallback)

    async def achoose(
        self,
        question_id: str,
        options: Sequence[Option],
        fallback: Fallback,
        **kwargs: Any,
    ) -> Choice:
        return self._reply(question_id, options, fallback)

    async def aevaluate(
        self,
        question_id: str,
        option_id: str,
        outcome: Any,
        *,
        evaluation_id: str,
    ) -> None:
        return None


def _acted(option_id: str) -> Choice:
    return Choice(option_id, True, "acted")


def _abstained(fallback: Fallback) -> Choice:
    """A runner that abstains still resolves to a concrete fallback answer."""
    return Choice(fallback(), False, "abstained: insufficient_confidence")


@pytest.fixture
def eg() -> Iterator[FakeRunner]:
    """One installed :class:`FakeRunner` per test, scoped and cleaned up."""
    runner = FakeRunner(answer=_acted("unset"))
    token = decide.use_runner(runner)
    yield runner
    decide._RUNNER.reset(token)


def test_no_runner_installed_is_exactly_the_fallback() -> None:
    assert decide.current_runner() is None
    choice = decide.choose("connector.tool", [Option("a")], lambda: "a")
    assert choice == Choice("a", False, "no_runner")


async def test_no_runner_installed_is_exactly_the_fallback_async() -> None:
    assert decide.current_runner() is None
    choice = await decide.achoose("connector.tool", [Option("a")], lambda: "a")
    assert choice == Choice("a", False, "no_runner")


def test_connector_tool_with_no_runner_returns_the_connector_s_pick() -> None:
    assert decide.current_runner() is None
    assert (
        bounded_choice("connector.tool", ["search", "get"], "get", connector="jira")
        == "get"
    )


def test_connector_tool_never_consults_decide_for_an_unlisted_pick(
    eg: FakeRunner,
) -> None:
    """A pick outside ``tools`` is a caller bug, not a decision -- no EG call."""
    assert (
        bounded_choice("connector.tool", ["search", "get"], "missing", connector="jira")
        == "missing"
    )
    assert eg.calls == []


def test_connector_tool_honors_an_executed_offered_option(eg: FakeRunner) -> None:
    eg.answer = _acted("search")
    assert (
        bounded_choice("connector.tool", ["search", "get"], "get", connector="jira")
        == "search"
    )
    assert eg.calls == [("connector.tool", ("get", "search"))]


def test_connector_tool_falls_back_when_decide_abstains(eg: FakeRunner) -> None:
    eg.answer = _abstained
    assert (
        bounded_choice("connector.tool", ["search", "get"], "get", connector="jira")
        == "get"
    )


def test_triage_with_no_runner_returns_the_deterministic_pick() -> None:
    assert decide.current_runner() is None
    assert (
        bounded_choice(
            "connector.triage",
            ["provision", "sync", "skip"],
            "sync",
            connector="freshrss-agent",
        )
        == "sync"
    )


def test_triage_never_consults_decide_for_an_undeclared_classification(
    eg: FakeRunner,
) -> None:
    """A pick outside ``actions`` is a caller bug, not a decision -- no EG call."""
    assert bounded_choice(
        "connector.triage",
        ["provision", "sync"],
        "unclassified",
        connector="freshrss-agent",
    ) == ("unclassified")
    assert eg.calls == []


def test_triage_honors_an_executed_offered_action(eg: FakeRunner) -> None:
    eg.answer = _acted("provision")
    assert bounded_choice(
        "connector.triage",
        ["provision", "sync", "skip"],
        "sync",
        connector="freshrss-agent",
    ) == ("provision")
    assert eg.calls == [("connector.triage", ("provision", "skip", "sync"))]


def test_triage_falls_back_when_decide_abstains(eg: FakeRunner) -> None:
    eg.answer = _abstained
    assert (
        bounded_choice(
            "connector.triage",
            ["provision", "sync", "skip"],
            "sync",
            connector="freshrss-agent",
        )
        == "sync"
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R006")
def test_triage_proposal_never_reaches_write_back(eg: FakeRunner) -> None:
    """A triage proposal is observational: it returns a plain action string,
    never a write-back change-set, and never asks the write-back question."""
    eg.answer = _acted("provision")
    chosen = bounded_choice(
        "connector.triage",
        ["provision", "sync", "skip"],
        "sync",
        connector="freshrss-agent",
    )
    assert chosen == "provision" and isinstance(chosen, str)
    assert eg.calls == [("connector.triage", ("provision", "skip", "sync"))]
    assert all(question != "connector.writeback" for question, _ in eg.calls)


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001")
def test_writeback_with_no_runner_defaults_to_no_write() -> None:
    assert decide.current_runner() is None
    proposals = {"close-incident": {"server": "servicenow-mcp", "tool": "t"}}
    assert propose_writeback("connector.writeback", proposals) == (NO_WRITE, None)


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001")
def test_writeback_is_only_ever_a_proposal(eg: FakeRunner) -> None:
    proposals = {"close-incident": {"server": "servicenow-mcp", "tool": "t"}}
    eg.answer = _acted("close-incident")
    assert propose_writeback("connector.writeback", proposals) == (
        "close-incident",
        proposals["close-incident"],
    )


def test_writeback_falls_back_to_no_write_when_decide_abstains(eg: FakeRunner) -> None:
    proposals = {"close-incident": {"server": "servicenow-mcp", "tool": "t"}}
    eg.answer = _abstained
    assert propose_writeback("connector.writeback", proposals) == (NO_WRITE, None)


def test_writeback_custom_default_is_the_fallback(eg: FakeRunner) -> None:
    proposals = {"close-incident": {"a": 1}, "escalate": {"b": 2}}
    eg.answer = _abstained
    assert propose_writeback("connector.writeback", proposals, default="escalate") == (
        "escalate",
        proposals["escalate"],
    )


def test_install_runner_is_process_wide_and_use_runner_is_context_scoped() -> None:
    process_runner = FakeRunner(answer=_acted("process"))
    decide.install_runner(process_runner)
    try:
        assert decide.current_runner() is process_runner
        scoped_runner = FakeRunner(answer=_acted("scoped"))
        token = decide.use_runner(scoped_runner)
        try:
            assert decide.current_runner() is scoped_runner
        finally:
            decide._RUNNER.reset(token)
        assert decide.current_runner() is process_runner
    finally:
        decide.install_runner(None)
    assert decide.current_runner() is None


def test_decision_runner_protocol_matches_a_plain_implementation() -> None:
    runner: DecisionRunner = FakeRunner(answer=_acted("x"))
    assert isinstance(runner, DecisionRunner)


def test_options_wire_shape_q32_scaling_and_declared_source() -> None:
    option = Option("search", numbers={"heuristic": 1.0}, texts={"note": "n"})
    wired = option.wire()
    assert wired["option_id"] == "search"
    assert wired["numbers"] == [{"key": "heuristic", "q32": q32(1.0)}]
    assert wired["texts"] == [{"key": "note", "text": "n"}]
    assert q32(1.0) == Q32_ONE
    assert q32(-1.0) == -Q32_ONE

    duplicate = Option("search", numbers={"heuristic": 0.0})
    ordered = unique_sorted([option, Option("get"), duplicate])
    assert [o.option_id for o in ordered] == ["get", "search"]
    assert ordered[1] is option, "first declaration of a repeated id wins"

    source = declared_source([Option("b"), Option("a")])
    assert source == {
        "source": "declared",
        "options": [Option("a").wire(), Option("b").wire()],
    }


def test_text_param_is_never_substituted_into_query_text() -> None:
    assert text_param("connector", "jira") == {
        "name": "connector",
        "value": {"type": "text", "value": "jira"},
    }


def _ranking_runner(answer: Any = None, *, fail: BaseException | None = None) -> Any:
    import asyncio

    from scion_sdk.decide.medulla import MedullaDecisionRunner
    from scion_sdk.decide.points import Binding, DecisionPoint, StaticBindings

    class _Transport:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def decide(self, request: Any) -> Any:
            self.requests.append(request)
            if fail is not None:
                raise fail
            return answer

        async def log(self, op: Any) -> Any:
            return None

        def run(self, call: Any) -> Any:
            return asyncio.run(call)

    transport = _Transport()
    point = DecisionPoint("connector.path_preference", "rank")
    runner = MedullaDecisionRunner(
        transport=transport,  # type: ignore[arg-type]
        tenant="t",
        bindings=StaticBindings(
            {"connector.path_preference": Binding(feature_schema={}, policy={})}
        ),
        points={point.question_id: point},
    )
    return runner, transport


def _advice(*ids: str) -> dict[str, Any]:
    scores = [{"option_id": i, "score": {"value": 100 - n}} for n, i in enumerate(ids)]
    outcome = {"outcome": "advisory", "scores": scores}
    return {"records": [{"record_digest": "sha256:" + "0" * 64, "outcome": outcome}]}


CANDIDATES = [
    RankCandidate("a", "Alpha", "first", "tool"),
    RankCandidate("b", "Beta", "second", "tool"),
    RankCandidate("c", "Gamma", "third", "tool"),
]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R006.1")
def test_ranked_choice_returns_the_engine_order_filtered_to_candidates() -> None:
    runner, transport = _ranking_runner(_advice("c", "zzz", "a"))
    previous = decide.use_runner(runner)
    try:
        order = ranked_choice(
            "connector.path_preference",
            CANDIDATES,
            lambda: ["a", "b", "c"],
            query="find the thing",
        )
    finally:
        decide.use_runner(None)
        del previous
    assert order == ["c", "a", "b"]
    request = transport.requests[0]
    assert "score" not in str(request["candidates"]).replace("scores", "")
    texts = {t["key"] for t in request["candidates"]["options"][0]["texts"]}
    assert texts == {"name", "description", "kind"}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R006.1")
def test_ranked_choice_answers_the_fallback_on_abstention_error_or_unknown_question() -> (
    None
):
    abstained = {
        "records": [
            {"outcome": {"outcome": "abstained", "reasons": [{"reason": "low"}]}}
        ]
    }
    for runner, question in (
        (_ranking_runner(abstained)[0], "connector.path_preference"),
        (_ranking_runner(fail=RuntimeError("down"))[0], "connector.path_preference"),
        (_ranking_runner(_advice("c"))[0], "no.such.question"),
    ):
        decide.use_runner(runner)
        try:
            assert ranked_choice(
                question, CANDIDATES, lambda: ["b", "a", "c"], query="q"
            ) == ["b", "a", "c"]
        finally:
            decide.use_runner(None)
    assert ranked_choice(
        "connector.path_preference", CANDIDATES, lambda: ["c", "b", "a"], query="q"
    ) == ["c", "b", "a"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R006.1")
def test_ranked_choice_sends_no_score_rank_or_threshold() -> None:
    runner, transport = _ranking_runner(_advice("a"))
    decide.use_runner(runner)
    try:
        ranked_choice(
            "connector.path_preference",
            CANDIDATES,
            lambda: ["a", "b", "c"],
            query="q",
            connector="demo",
        )
    finally:
        decide.use_runner(None)
    request = transport.requests[0]
    for option in request["candidates"]["options"]:
        assert option["numbers"] == []
    assert {p["name"] for p in request["params"]} == {"query", "connector"}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R006")
def test_ranking_is_observational_and_never_authorizes(eg: FakeRunner) -> None:
    assert ranked_choice("q", CANDIDATES, lambda: ["a", "b", "c"], query="x") == [
        "a",
        "b",
        "c",
    ]


def test_ranking_reading_names_the_ordered_offered_ids() -> None:
    from scion_sdk.decide import Ranking, choose_many
    from scion_sdk.decide.outcome import read_ranking

    reading = read_ranking(_advice("b", "x", "a"), frozenset({"a", "b"}))
    assert reading.option_id == "b\na"
    assert choose_many("q", [Option("a")], lambda: ["a"]) == Ranking(
        ("a",), False, "no_runner"
    )
