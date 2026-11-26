"""SCION-CONNECTOR-CONTROL-R014.21: the reusable A2A protocol pieces."""

from __future__ import annotations

import builtins
import json
import sys
import time

import httpx
import pytest

from scion_sdk.a2a import (
    A2AClient,
    A2AMessage,
    A2ATask,
    AgentCard,
    AgentSkill,
)


def _transport(handler):
    return httpx.MockTransport(handler)


def test_r014_21_card_round_trips_the_wire_aliases() -> None:
    card = AgentCard(
        name="peer",
        url="https://peer.example.test",
        skills=[AgentSkill(id="s", name="Search", tags=["search"])],
    )
    wire = card.wire()
    assert wire["defaultInputModes"] == ["text"]
    assert wire["skills"][0]["inputModes"] == ["text"]
    assert AgentCard.model_validate(wire) == card


def test_r014_21_task_reports_the_last_agent_reply() -> None:
    task = A2ATask.model_validate(
        {
            "id": "t1",
            "status": {"state": "completed"},
            "history": [
                A2AMessage.user_text("hi").wire(),
                {"role": "agent", "parts": [{"kind": "text", "text": "hello"}]},
            ],
        }
    )
    assert not task.pending
    assert task.last_agent_message().text() == "hello"


@pytest.mark.asyncio
async def test_r014_21_client_sends_then_polls_until_completed() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["method"])
        if body["method"] == "message/send":
            result = {"id": "t1", "status": {"state": "submitted"}}
        else:
            result = {
                "id": "t1",
                "status": {"state": "completed"},
                "history": [
                    {"role": "agent", "parts": [{"kind": "text", "text": "done"}]}
                ],
            }
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": result}
        )

    client = A2AClient(poll_interval=0.01, transport=_transport(handler))
    result = await client.send("https://peer.example.test/", "do it")
    assert result.ok and result.content == "done"
    assert calls == ["message/send", "tasks/get"]


@pytest.mark.asyncio
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.21.3")
@pytest.mark.parametrize("violation", ["id", "version", "task", "both"])
async def test_a2a_poll_rejects_uncorrelated_response(violation):
    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "message/send":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"id": "original", "status": {"state": "submitted"}},
                },
            )
        result = {
            "id": "different" if violation == "task" else "original",
            "status": {"state": "completed"},
            "history": [
                {"role": "agent", "parts": [{"kind": "text", "text": "private"}]}
            ],
        }
        reply = {
            "jsonrpc": "1.0" if violation == "version" else "2.0",
            "id": 1 if violation == "id" else body["id"],
            "result": result,
        }
        if violation == "both":
            reply["error"] = {"message": "private"}
        return httpx.Response(200, json=reply)

    result = await A2AClient(poll_interval=0.001, transport=_transport(handler)).send(
        "https://peer.example.test/", "request"
    )
    assert result.error == "A2A communication failed (ValueError)"
    assert "private" not in str(result)


@pytest.mark.asyncio
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.21.3")
async def test_a2a_send_rejects_boolean_response_id():
    transport = _transport(
        lambda _request: httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": True,
                "result": {"id": "t", "status": {"state": "completed"}},
            },
        )
    )
    result = await A2AClient(transport=transport).send(
        "https://peer.example.test/", "x"
    )
    assert result.error == "A2A communication failed (ValueError)"


@pytest.mark.asyncio
async def test_r014_21_client_never_echoes_a_remote_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "secret"}}
        )

    result = await A2AClient(transport=_transport(handler)).send("https://p", "x")
    assert result.error == "A2A peer rejected the request"
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_r014_21_fetch_card_rejects_an_invalid_card() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/agent-card.json"
        return httpx.Response(200, json={"name": ""})

    assert (
        await A2AClient(transport=_transport(handler)).fetch_card("https://p") is None
    )


def test_r014_21_app_factory_names_the_a2a_extra(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"fasta2a", "starlette"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "fasta2a", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    from scion_sdk.a2a.app import require_a2a_extra

    with pytest.raises(ImportError, match=r"scion-sdk\[a2a\]"):
        require_a2a_extra("create_a2a_app")


def test_r014_21_base_import_does_not_load_the_server_stack() -> None:
    import subprocess

    probe = (
        "import sys, scion_sdk.a2a; "
        "assert 'scion_sdk.a2a.app' not in sys.modules; "
        "assert 'fasta2a' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)


def test_r014_21_status_parts_and_result_shapes() -> None:
    from typing import get_args

    from scion_sdk.a2a import A2AResult, A2ATaskStatus, TaskState, TextPart
    from scion_sdk.a2a.client import CARD_PATH

    assert A2ATaskStatus().state == "unknown"
    assert "completed" in get_args(TaskState)
    with pytest.raises(ValueError):
        A2ATaskStatus.model_validate({"state": "exploded"})
    message = A2AMessage(role="agent", parts=[TextPart(text="a"), TextPart(text="b")])
    assert message.text() == "ab" and message.parts[0].model_dump()["kind"] == "text"
    assert A2AResult(content="x").ok and not A2AResult(error="refused").ok
    assert CARD_PATH == "/.well-known/agent-card.json"


def test_r014_21_app_factory_builds_with_the_extra() -> None:
    pytest.importorskip("fasta2a")
    from scion_sdk.a2a import create_a2a_app

    assert callable(create_a2a_app)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.21.1")
@pytest.mark.parametrize("path", ("card", "send", "poll"))
async def test_streaming_response_stops_at_bound(path: str) -> None:
    from scion_sdk.a2a.client import _MAX_RESPONSE_BYTES

    class Oversized(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.chunks = 0
            self.closed = False

        async def __aiter__(self):
            for _ in range(6):
                self.chunks += 1
                yield b"x" * (_MAX_RESPONSE_BYTES // 4)

        async def aclose(self) -> None:
            self.closed = True

    stream = Oversized()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        if path == "poll" and json.loads(request.content)["method"] == "message/send":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"id": "t", "status": {"state": "submitted"}},
                },
            )
        return httpx.Response(200, stream=stream)

    client = A2AClient(poll_interval=0.01, transport=_transport(handler))
    if path == "card":
        assert await client.fetch_card("https://peer.example") is None
    else:
        result = await client.send("https://peer.example", "hello")
        assert result.error == "A2A communication failed (ValueError)"
    assert stream.chunks == 5
    assert stream.closed


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.21.1")
async def test_compressed_response_is_refused_without_reading() -> None:
    class Compressed(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.read = False
            self.closed = False

        async def __aiter__(self):
            self.read = True
            yield b"secret"

        async def aclose(self) -> None:
            self.closed = True

    stream = Compressed()
    client = A2AClient(
        transport=_transport(
            lambda request: httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, stream=stream
            )
        )
    )
    result = await client.send("https://peer.example", "hello")
    assert result.error == "A2A communication failed (ValueError)"
    assert not stream.read and stream.closed


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R014.21.2")
def test_a2a_app_serves_tasks_using_caller_broker_and_storage() -> None:
    import time

    from fasta2a.broker import InMemoryBroker
    from fasta2a.storage import InMemoryStorage
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from starlette.testclient import TestClient

    from scion_sdk.a2a import create_a2a_app

    class Storage(InMemoryStorage):
        """The in-memory task double with the caller-scoped context page."""

        def __init__(self) -> None:
            super().__init__()
            self._turn_logs: dict[str, list[dict]] = {}

        async def list_contexts(self, caller, cursor, limit):
            return [], None

        async def append_turn_event(self, task_id, event):
            log = self._turn_logs.setdefault(task_id, [])
            log.append({**event, "seq": len(log) + 1})
            return len(log)

        async def turn_events_after(self, caller, task_id, after_seq):
            return []

    storage = Storage()
    broker = InMemoryBroker()
    agent = Agent(TestModel(custom_output_text="finished"))
    app = create_a2a_app(
        agent,
        name="fixture",
        url="http://testserver",
        version="1",
        storage=storage,
        broker=broker,
    )
    assert app.task_manager.storage is storage
    assert app.task_manager.broker is broker
    with TestClient(app) as client:
        card = client.get("/.well-known/agent-card.json")
        assert card.status_code == 200
        assert card.json()["name"] == "fixture"
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {
                        "kind": "message",
                        "messageId": "m1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hello"}],
                    }
                },
            },
        )
        assert response.status_code == 200
        task_id = response.json()["result"]["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tasks/get",
                    "params": {"id": task_id},
                },
            )
            task = response.json()["result"]
            if task["status"]["state"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        assert task["status"]["state"] == "completed"
        assert task["history"][-1]["parts"][0]["text"] == "finished"
        assert task_id in storage.tasks


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.1")
def test_tasks_list_serves_durable_storage() -> None:
    pytest.importorskip("fasta2a")
    from fasta2a.broker import InMemoryBroker
    from fasta2a.storage import InMemoryStorage
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from starlette.testclient import TestClient

    from scion_sdk.a2a import create_a2a_app
    from scion_sdk.a2a.app import ForeignContextError

    class DurableStorage(InMemoryStorage):
        """Caller-scoped context store on the in-memory task test double."""

        def __init__(self) -> None:
            super().__init__()
            self._contexts = {
                "alice": [{"contextId": "ctx-a"}, {"contextId": "ctx-b"}],
                "bob": [{"contextId": "ctx-c"}],
            }
            self._turn_logs: dict[str, list[dict]] = {}

        async def list_contexts(self, caller, cursor, limit):
            if cursor is not None and cursor != f"cursor-{caller}":
                raise ForeignContextError
            page = self._contexts.get(caller, [])[:limit]
            next_cursor = f"cursor-{caller}" if len(page) == limit else None
            return page, next_cursor

        async def append_turn_event(self, task_id, event):
            log = self._turn_logs.setdefault(task_id, [])
            log.append({**event, "seq": len(log) + 1})
            return len(log)

        async def turn_events_after(self, caller, task_id, after_seq):
            task = self.tasks.get(task_id)
            owned = {entry["contextId"] for entry in self._contexts.get(caller, [])}
            if task is None or task.get("context_id") not in owned:
                raise ForeignContextError
            return [
                entry
                for entry in self._turn_logs.get(task_id, [])
                if entry["seq"] > after_seq
            ]

    app = create_a2a_app(
        Agent(TestModel(custom_output_text="done")),
        name="fixture",
        url="http://testserver",
        version="1",
        storage=DurableStorage(),
        broker=InMemoryBroker(),
    )
    with TestClient(app) as client:
        # The page lists the caller's contexts; no more than one page.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/list",
                "params": {"caller": "alice", "limit": 10},
            },
        )
        assert reply.status_code == 200
        result = reply.json()["result"]
        assert result["tasks"] == [{"contextId": "ctx-a"}, {"contextId": "ctx-b"}]
        assert result["next_cursor"] is None

        # A bounded page carries the continuation cursor.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tasks/list",
                "params": {"caller": "alice", "limit": 1},
            },
        )
        assert reply.json()["result"]["tasks"] == [{"contextId": "ctx-a"}]
        assert reply.json()["result"]["next_cursor"] == "cursor-alice"

        # A context the caller does not own is refused with -32001.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tasks/list",
                "params": {"caller": "bob", "contextId": "ctx-a"},
            },
        )
        assert reply.json()["error"] == {"code": -32001, "message": "foreign context"}

        # A cursor from another caller is refused with the same code, no data.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tasks/list",
                "params": {"caller": "bob", "cursor": "cursor-alice"},
            },
        )
        assert reply.json()["error"]["code"] == -32001
        assert "result" not in reply.json()

        # Malformed params refuse without echoing caller input.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tasks/list",
                "params": {"caller": "alice", "limit": 101},
            },
        )
        assert reply.json()["error"] == {"code": -32602, "message": "Invalid params"}

        # The protocol methods still reach the agent.
        response = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "message/send",
                "params": {
                    "message": {
                        "kind": "message",
                        "messageId": "m1",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hello"}],
                    }
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["status"]["state"] == "submitted"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.1")
def test_a2a_app_refuses_a_storage_without_list_contexts() -> None:
    pytest.importorskip("fasta2a")
    from fasta2a.broker import InMemoryBroker
    from fasta2a.storage import InMemoryStorage
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from scion_sdk.a2a import create_a2a_app

    with pytest.raises(TypeError, match="list_contexts"):
        create_a2a_app(
            Agent(TestModel(custom_output_text="done")),
            name="fixture",
            url="http://testserver",
            version="1",
            storage=InMemoryStorage(),
            broker=InMemoryBroker(),
        )


def _sse_frames(response) -> list:
    frames = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            frames.append(json.loads(line[len("data: ") :]))
    return frames


def _stream_request(message_id: str = "m1", context_id: str = "ctx-a") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/stream",
        "params": {
            "message": {
                "kind": "message",
                "messageId": message_id,
                "role": "user",
                "contextId": context_id,
                "parts": [{"kind": "text", "text": "hello"}],
            }
        },
    }


def _turn_storage():
    from fasta2a.storage import InMemoryStorage

    from scion_sdk.a2a.app import ForeignContextError

    class TurnStorage(InMemoryStorage):
        """The in-memory task double with the caller page and the durable
        turn log."""

        def __init__(self) -> None:
            super().__init__()
            self._contexts = {"alice": [{"contextId": "ctx-a"}]}
            self._logs: dict[str, list[dict]] = {}

        async def list_contexts(self, caller, cursor, limit):
            if cursor is not None and cursor != f"cursor-{caller}":
                raise ForeignContextError
            page = self._contexts.get(caller, [])[:limit]
            next_cursor = f"cursor-{caller}" if len(page) == limit else None
            return page, next_cursor

        async def append_turn_event(self, task_id, event):
            log = self._logs.setdefault(task_id, [])
            log.append({**event, "seq": len(log) + 1})
            return len(log)

        async def turn_events_after(self, caller, task_id, after_seq):
            task = self.tasks.get(task_id)
            owned = {entry["contextId"] for entry in self._contexts.get(caller, [])}
            if task is None or task.get("context_id") not in owned:
                raise ForeignContextError
            return [
                entry
                for entry in self._logs.get(task_id, [])
                if entry["seq"] > after_seq
            ]

    return TurnStorage()


def _streaming_app(storage, agent):
    from fasta2a.broker import InMemoryBroker

    from scion_sdk.a2a import create_a2a_app

    return create_a2a_app(
        agent,
        name="fixture",
        url="http://testserver",
        version="1",
        storage=storage,
        broker=InMemoryBroker(),
    )


def _wait_a_turn(storage: dict, task_id: str) -> None:
    """Block until the background turn has reached a terminal state."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = storage.get(task_id)
        if task is not None and task["status"]["state"] in {
            "completed",
            "failed",
        }:
            return
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach a terminal state")


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.2")
def test_resubscribe_replays_events_after_cursor() -> None:
    pytest.importorskip("fasta2a")
    from pydantic import TypeAdapter
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.toolsets.function import FunctionToolset
    from starlette.testclient import TestClient

    from scion_sdk.a2a import TurnEvent

    def wait() -> str:
        """Pause before the reply so the stream spans time."""
        time.sleep(0.2)
        return "paused"

    storage = _turn_storage()
    agent = Agent(
        TestModel(call_tools=["wait"], custom_output_text="done"),
        toolsets=[FunctionToolset([wait])],
    )
    app = _streaming_app(storage, agent)
    adapter = TypeAdapter(TurnEvent)
    with TestClient(app) as client:
        # Stream one turn; drop the connection after the first event.
        with client.stream("POST", "/", json=_stream_request()) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream"
            received = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    received.append(json.loads(line[len("data: ") :]))
                if received:
                    break
        assert received[0]["kind"] == "tool-call"
        task_id = received[0]["taskId"]
        _wait_a_turn(storage.tasks, task_id)

        # Every streamed frame is a valid turn event on the durable sequence.
        for frame in received:
            adapter.validate_json(json.dumps(frame))
        cursor = received[-1]["seq"]

        # Resubscribe from the cursor: the tail replays once, in order.
        with client.stream(
            "POST",
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tasks/resubscribe",
                "params": {"id": task_id, "caller": "alice", "afterSeq": cursor},
            },
        ) as response:
            assert response.status_code == 200
            tail = _sse_frames(response)
        for frame in tail:
            adapter.validate_json(json.dumps(frame))
        assert all(frame["seq"] > cursor for frame in tail)
        assert [frame["seq"] for frame in tail] == sorted(
            frame["seq"] for frame in tail
        )
        # No gaps and no duplicates across the live stream and the replay.
        assert [frame["seq"] for frame in received + tail] == list(
            range(1, len(received) + len(tail) + 1)
        )
        assert tail[-1]["kind"] == "final-state"
        assert tail[-1]["state"] == "completed"
        # The turn survived the drop and completed like message/send would.
        task = storage.tasks[task_id]
        assert task["status"]["state"] == "completed"
        assert task["history"][-1]["parts"][0]["text"] == "done"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.2")
def test_stream_emits_the_turn_events_in_order() -> None:
    pytest.importorskip("fasta2a")
    from pydantic import TypeAdapter
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.toolsets.function import FunctionToolset
    from starlette.testclient import TestClient

    from scion_sdk.a2a import TurnEvent

    def wait() -> str:
        """A tool the turn calls before its reply."""
        return "paused"

    storage = _turn_storage()
    agent = Agent(
        TestModel(call_tools=["wait"], custom_output_text="done"),
        toolsets=[FunctionToolset([wait])],
    )
    app = _streaming_app(storage, agent)
    adapter = TypeAdapter(TurnEvent)
    with (
        TestClient(app) as client,
        client.stream("POST", "/", json=_stream_request()) as response,
    ):
        frames = _sse_frames(response)
    assert frames, "the stream produced no events"
    for frame in frames:
        adapter.validate_json(json.dumps(frame))
    kinds = [frame["kind"] for frame in frames]
    assert kinds[0] == "tool-call"
    assert kinds.index("tool-result") == 1
    assert kinds.count("text-delta") == 2
    assert (
        "".join(frame["text"] for frame in frames if frame["kind"] == "text-delta")
        == "done"
    )
    assert kinds[-1] == "final-state"
    assert frames[-1]["state"] == "completed"
    assert [frame["seq"] for frame in frames] == list(range(1, len(frames) + 1))


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.2")
def test_resubscribe_refuses_foreign_and_malformed_requests() -> None:
    pytest.importorskip("fasta2a")
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from starlette.testclient import TestClient

    storage = _turn_storage()
    app = _streaming_app(storage, Agent(TestModel(custom_output_text="done")))
    with TestClient(app) as client:
        # Stream one turn to completion so the log is non-empty.
        with client.stream("POST", "/", json=_stream_request()) as response:
            frames = _sse_frames(response)
        assert frames[-1]["kind"] == "final-state"
        task_id = frames[0]["taskId"]

        # A caller that does not own the task's context is refused, no data.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tasks/resubscribe",
                "params": {"id": task_id, "caller": "bob"},
            },
        )
        assert reply.status_code == 200
        assert reply.json()["error"] == {
            "code": -32001,
            "message": "foreign context",
        }
        assert "result" not in reply.json()

        # A task the caller does not have is refused the same way, so
        # existence never leaks.
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tasks/resubscribe",
                "params": {"id": "unknown", "caller": "alice"},
            },
        )
        assert reply.json()["error"]["code"] == -32001

        # Malformed params refuse without running the replay.
        for params in (
            {"caller": "alice"},
            {"id": task_id},
            {"id": task_id, "caller": "alice", "afterSeq": "3"},
            {"id": task_id, "caller": "alice", "afterSeq": True},
            {"id": task_id, "caller": "alice", "afterSeq": -1},
            {"id": 7, "caller": "alice"},
        ):
            reply = client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tasks/resubscribe",
                    "params": params,
                },
            )
            assert reply.json()["error"] == {
                "code": -32602,
                "message": "Invalid params",
            }

        # An omitted cursor replays the whole log, in order.
        with client.stream(
            "POST",
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tasks/resubscribe",
                "params": {"id": task_id, "caller": "alice"},
            },
        ) as response:
            whole = _sse_frames(response)
        assert [frame["seq"] for frame in whole] == [frame["seq"] for frame in frames]

        # A stream request without a message is refused before the task
        # store is touched.
        before = len(storage.tasks)
        reply = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "message/stream",
                "params": {},
            },
        )
        assert reply.json()["error"]["code"] == -32602
        assert len(storage.tasks) == before


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.2")
def test_a2a_app_refuses_a_storage_without_turn_events() -> None:
    pytest.importorskip("fasta2a")
    from fasta2a.broker import InMemoryBroker
    from fasta2a.storage import InMemoryStorage
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from scion_sdk.a2a import create_a2a_app

    class ListOnlyStorage(InMemoryStorage):
        async def list_contexts(self, caller, cursor, limit):
            return [], None

    with pytest.raises(TypeError, match="append_turn_event"):
        create_a2a_app(
            Agent(TestModel(custom_output_text="done")),
            name="fixture",
            url="http://testserver",
            version="1",
            storage=ListOnlyStorage(),
            broker=InMemoryBroker(),
        )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.2")
def test_turn_event_wire_models_round_trip() -> None:
    from pydantic import TypeAdapter

    from scion_sdk.a2a import (
        FinalStateEvent,
        TextDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        TurnEvent,
    )

    adapter = TypeAdapter(TurnEvent)
    delta = adapter.validate_json(
        '{"kind": "text-delta", "taskId": "t1", "contextId": "c1", "seq": 1, "text": "hi"}'
    )
    assert isinstance(delta, TextDeltaEvent) and delta.text == "hi"
    call = adapter.validate_json(
        '{"kind": "tool-call", "taskId": "t1", "contextId": "c1", "seq": 2, "toolName": "t", "arguments": {"x": 1}}'
    )
    assert isinstance(call, ToolCallEvent) and call.arguments == {"x": 1}
    result = adapter.validate_json(
        '{"kind": "tool-result", "taskId": "t1", "contextId": "c1", "seq": 3, "toolName": "t", "result": "ok"}'
    )
    assert isinstance(result, ToolResultEvent) and result.result == "ok"
    final = adapter.validate_json(
        '{"kind": "final-state", "taskId": "t1", "contextId": "c1", "seq": 4, "state": "completed"}'
    )
    assert isinstance(final, FinalStateEvent) and final.state == "completed"
    # A kind outside the turn-event set is refused.
    with pytest.raises(ValueError):
        adapter.validate_json(
            '{"kind": "artifact-update", "taskId": "t1", "contextId": "c1", "seq": 5}'
        )
    # The cursor is a 1-based durable sequence; zero is not a sequence.
    with pytest.raises(ValueError):
        adapter.validate_json(
            '{"kind": "text-delta", "taskId": "t1", "contextId": "c1", "seq": 0, "text": "x"}'
        )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R017.2")
def test_card_advertises_streaming_from_the_wired_turn_log() -> None:
    pytest.importorskip("fasta2a")
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from starlette.testclient import TestClient

    from scion_sdk.a2a.app import streaming_wired

    storage = _turn_storage()
    # The card reports what is wired, not a constant: the turn log is wired.
    assert streaming_wired(storage) is True
    # A storage without the turn-event log wires no streaming.
    assert streaming_wired(object()) is False
    app = _streaming_app(storage, Agent(TestModel(custom_output_text="done")))
    with TestClient(app) as client:
        card = client.get("/.well-known/agent-card.json").json()
        assert card["capabilities"]["streaming"] is True
        assert card["capabilities"]["pushNotifications"] is False
        assert card["name"] == "fixture"
