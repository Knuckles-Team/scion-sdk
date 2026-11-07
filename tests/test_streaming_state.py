"""The WebSocket stream machine (SCION-REPOSITORY-TRANSPORT-R008)."""

from __future__ import annotations

from importlib import resources
from typing import Any

import pytest

from scion_sdk._stream_websocket import StreamEdge
from scion_sdk._turtle import parse_graph
from scion_sdk.runner.backoff import Backoff
from scion_sdk.runner.health_state import RunnerHealth
from scion_sdk.streaming import (
    STREAM_EDGES,
    StreamBus,
    StreamEvent,
    StreamState,
    StreamTransitionRefused,
    WebSocketStreamAdapter,
    stream_transition,
)
from scion_sdk.testing.sinks import InMemorySink

pytestmark = [
    pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R008"),
    pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R008.1"),
    pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R008.2"),
    pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R008.3"),
    pytest.mark.spec("SCION-REPOSITORY-TRANSPORT-R008.4"),
]


_LEGAL = {(edge.source, edge.event) for edge in STREAM_EDGES}
_ILLEGAL = [
    pytest.param(state, event, id=f"{state.value.lower()}-{event.value}")
    for state in StreamState
    for event in StreamEvent
    if (state, event) not in _LEGAL
]


class _Socket:
    def __init__(self, frames: list[str] | None = None) -> None:
        self.frames = frames or []
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for frame in self.frames:
            yield frame

    async def close(self) -> None:
        self.closed = True


def _adapter(**extra: Any) -> tuple[WebSocketStreamAdapter, list[_Socket]]:
    opened: list[_Socket] = []

    async def connect(_url: str) -> _Socket:
        opened.append(_Socket())
        return opened[-1]

    return (
        WebSocketStreamAdapter(StreamBus(), "wss://feed.invalid", connect, **extra),
        opened,
    )


@pytest.mark.parametrize("edge", [pytest.param(e, id=e.id) for e in STREAM_EDGES])
def test_each_legal_edge_moves_state(edge) -> None:
    assert stream_transition(edge.source, edge.event) is edge.target


@pytest.mark.parametrize(("state", "event"), _ILLEGAL)
def test_every_illegal_pair_is_refused_with_the_typed_error(state, event) -> None:
    with pytest.raises(StreamTransitionRefused) as raised:
        stream_transition(state, event)
    refusal = raised.value
    assert refusal.code == "STREAM_TRANSITION_REFUSED"
    assert (refusal.machine, refusal.from_state, refusal.event) == (
        "WebSocketStream",
        state.value,
        event.value,
    )


def test_every_edge_and_refusal_of_the_stream_machine() -> None:
    assert len(STREAM_EDGES) == 11
    assert all(isinstance(edge, StreamEdge) for edge in STREAM_EDGES)
    assert len(_ILLEGAL) == len(StreamState) * len(StreamEvent) - 11
    for event in StreamEvent:
        assert (StreamState.CLOSED, event) not in _LEGAL


async def test_double_connect_and_listen_while_closed_are_refused_without_leak() -> (
    None
):
    adapter, opened = _adapter()
    assert await adapter.connect() is True
    assert adapter.state is StreamState.OPEN and adapter.is_connected
    with pytest.raises(StreamTransitionRefused):
        await adapter.connect()
    assert len(opened) == 1 and adapter.state is StreamState.OPEN

    await adapter.disconnect()
    assert adapter.state is StreamState.CLOSED and opened[0].closed
    assert not adapter.is_connected and adapter._connection is None
    with pytest.raises(StreamTransitionRefused) as raised:
        await adapter.listen()
    assert (raised.value.from_state, raised.value.event) == ("Closed", "listen")
    with pytest.raises(StreamTransitionRefused):
        await adapter.connect()
    assert len(opened) == 1


async def test_an_error_in_open_drops_the_handle_and_fails() -> None:
    class _Broken(_Socket):
        async def _iterate(self):
            raise OSError("reset")
            yield ""

    async def connect(_url: str) -> _Socket:
        return _Broken()

    adapter = WebSocketStreamAdapter(StreamBus(), "wss://x", connect)
    assert await adapter.connect()
    await adapter.listen()
    assert adapter.state is StreamState.FAILED and adapter._connection is None


async def test_failed_stream_retries_with_backoff_and_marks_lifecycle() -> None:
    health = RunnerHealth(
        sink=InMemorySink(), liveness_window_seconds=30.0, degraded_failures=2
    )
    health.sync_registry(("feed",))
    health.record_credentials("feed", ok=True)
    attempts = {"n": 0}
    delays: list[float] = []

    async def connect(_url: str) -> _Socket:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("refused")
        return _Socket()

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    adapter = WebSocketStreamAdapter(
        StreamBus(),
        "wss://x",
        connect,
        health=health,
        connector="feed",
        backoff=Backoff(1.0, 8.0),
        sleep=sleep,
    )

    def lifecycle() -> str:
        return health._connectors["feed"].lifecycle_state.value

    assert await adapter.connect() is False
    assert (adapter.state, lifecycle()) == (StreamState.FAILED, "Degraded")
    assert await adapter.retry() is False
    assert (adapter.state, lifecycle()) == (StreamState.FAILED, "Backoff")
    assert await adapter.retry() is True
    assert (adapter.state, lifecycle()) == (StreamState.OPEN, "Ready")
    assert delays == [1.0, 2.0] and adapter.retries == 2
    with pytest.raises(StreamTransitionRefused):
        await adapter.retry()


def test_turtle_matches_the_table_and_extends_core() -> None:
    text = (
        resources.files("scion_sdk") / "ontology" / "stream_transport.ttl"
    ).read_text(encoding="utf-8")
    assert "owl:imports" not in text
    graph = parse_graph(text)
    kg = "http://knuckles.team/kg#"
    st = "https://knuckles-team.github.io/scion-sdk/stream#"
    sub = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
    assert graph[st + "WebSocketStream"][sub] == [kg + "StateMachine"]
    for state in StreamState:
        assert kg + "State" in graph[st + state.value][sub]
    for event in StreamEvent:
        assert graph[f"{st}{event.value.capitalize()}Event"][sub] == [kg + "Event"]
    assert graph[st + "StreamTransitionRefused"][sub] == [kg + "TransitionRefused"]
    for edge in STREAM_EDGES:
        node = graph[f"{st}Edge_{edge.id}"]
        assert node[sub] == [kg + "Transition"]
        assert node[kg + "fromState"] == [st + edge.source.value]
        assert node[kg + "toState"] == [st + edge.target.value]
        assert node[kg + "firedBy"] == [f"{st}{edge.event.value.capitalize()}Event"]
    assert len([s for s in graph if s.startswith(st + "Edge_")]) == len(STREAM_EDGES)
