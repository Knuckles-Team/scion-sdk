"""Server-side A2A application factory (needs the ``a2a`` extra).

CONCEPT:SCION-CONNECTOR-CONTROL-R014.21

Builds a fasta2a ASGI app for a pydantic-ai agent. The caller supplies the
broker and a durable :class:`Storage`; this SDK holds no task store, and an
in-memory storage is a test double only. Besides the protocol methods, the
app serves ``tasks/list`` from the storage's ``list_contexts``: one page of
the caller's contexts with a continuation cursor, and ``-32001`` when the
caller names a context or a cursor it does not own. ``message/stream`` runs
the turn as a durable background job: every event it produces (text-delta,
tool-call, tool-result, final-state) is appended to the storage's turn log
with a sequence number and streamed to the caller as server-sent events, so
a caller that drops the connection can replay the tail for
``tasks/resubscribe`` from an ``afterSeq`` cursor. A host mounts the
returned app behind its own admission and authentication.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, Protocol, runtime_checkable

from .models import AgentSkill

__all__ = [
    "ForeignContextError",
    "Storage",
    "create_a2a_app",
    "require_a2a_extra",
    "streaming_wired",
]

#: JSON-RPC code for a caller naming a context or cursor it does not own.
_FOREIGN_CONTEXT = -32001
_INVALID_PARAMS = -32602
_INTERNAL = -32000
_DEFAULT_PAGE = 50
_MAX_PAGE = 100
_MAX_CALLER_LEN = 256
_MAX_CURSOR_LEN = 1024
_MAX_CONTEXT_ID_LEN = 1024
_MAX_TASK_ID_LEN = 1024


class ForeignContextError(ValueError):
    """The caller named a context or cursor that does not belong to it."""


@runtime_checkable
class Storage(Protocol):
    """The durable storage's caller-scoped page and turn-event log, on top
    of the fasta2a ``Storage`` interface the application itself requires.

    ``create_a2a_app`` serves ``tasks/list`` from :meth:`list_contexts`,
    streams ``message/stream`` through :meth:`append_turn_event`, and
    replays ``tasks/resubscribe`` from :meth:`turn_events_after`. An
    in-memory implementation is a test double only; production callers
    supply durable storage.
    """

    async def list_contexts(
        self, caller: str, cursor: str | None, limit: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Page ``caller``'s contexts from ``cursor`` with at most ``limit`` entries, plus the continuation cursor.

        Raises:
            ForeignContextError: a cursor that belongs to another caller.
        """
        ...

    async def append_turn_event(self, task_id: str, event: dict[str, Any]) -> int:
        """Append one turn event to the task's durable log, in order, and return the 1-based sequence it received.

        The event holds the wire fields without ``seq``; the storage
        assigns and keeps the sequence.
        """
        ...

    async def turn_events_after(
        self, caller: str, task_id: str, after_seq: int
    ) -> list[dict[str, Any]]:
        """The task's turn events with sequence greater than ``after_seq``, in sequence order, each carrying ``seq``.

        Raises:
            ForeignContextError: the caller does not own the task's
            context, or the task is not visible to it.
        """
        ...


async def _read_body(receive: Callable[[], Awaitable[dict[str, Any]]]) -> bytes:
    """Buffer one HTTP request body from the ASGI ``receive`` channel."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay_body(body: bytes) -> Callable[[], Awaitable[dict[str, Any]]]:
    """An ASGI ``receive`` that replays one already-buffered body."""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _json_object(body: bytes) -> Any:
    try:
        return json.loads(body)
    except ValueError:
        return None


async def _send_json(
    send: Callable[[dict[str, Any]], Awaitable[None]], payload: dict[str, Any]
) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _sse_start(send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    """Open a server-sent-events response body."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-cache"),
            ],
        }
    )


async def _sse_frame(
    send: Callable[[dict[str, Any]], Awaitable[None]], event: dict[str, Any]
) -> None:
    """Send one turn event as an SSE ``data:`` frame."""
    frame = f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()
    await send({"type": "http.response.body", "body": frame, "more_body": True})


async def _sse_end(send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    """Close a server-sent-events response body."""
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def _error_reply(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def _tasks_list_reply(storage: Any, request: dict[str, Any]) -> dict[str, Any]:
    request_id = request.get("id")
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}
    caller = params.get("caller")
    context_id = params.get("contextId")
    cursor = params.get("cursor")
    limit = params.get("limit", _DEFAULT_PAGE)
    if (
        not isinstance(caller, str)
        or not caller
        or len(caller) > _MAX_CALLER_LEN
        or (
            context_id is not None
            and (
                not isinstance(context_id, str) or len(context_id) > _MAX_CONTEXT_ID_LEN
            )
        )
        or (
            cursor is not None
            and (not isinstance(cursor, str) or len(cursor) > _MAX_CURSOR_LEN)
        )
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _MAX_PAGE
    ):
        return _error_reply(request_id, _INVALID_PARAMS, "Invalid params")
    try:
        page, next_cursor = await storage.list_contexts(caller, cursor, limit)
    except ForeignContextError:
        # The cursor does not belong to this caller: refuse with no data.
        return _error_reply(request_id, _FOREIGN_CONTEXT, "foreign context")
    entries = [entry for entry in page if isinstance(entry, dict)]
    if context_id is not None:
        own = [entry for entry in entries if entry.get("contextId") == context_id]
        if not own:
            # The caller named a context that is not among its own page.
            return _error_reply(request_id, _FOREIGN_CONTEXT, "foreign context")
        entries = own
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"tasks": entries, "next_cursor": next_cursor},
    }


def _build_turn_worker(agent: Any, broker: Any, storage: Any) -> Any:
    """The app's worker, with the streaming turn added to it.

    The turn reuses the worker's history, artifact, and response-part
    handling, so ``message/stream`` leaves the task store in the same
    shape ``message/send`` does. Each model event it maps to a wire turn
    event (text-delta, tool-call, tool-result) is handed to ``emit``; the
    final-state event the caller receives comes from the gate, after the
    task store is updated.
    """
    from fasta2a.pydantic_ai import AgentWorker
    from fasta2a.schema import Message
    from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent
    from pydantic_ai.messages import (
        ModelRequest,
        PartDeltaEvent,
        TextPartDelta,
        ToolReturnPart,
    )
    from pydantic_core import to_jsonable_python

    # fasta2a ships no types; the worker's base class is untyped.
    class _TurnWorker(AgentWorker):  # type: ignore[misc]
        async def stream_turn(
            self,
            task_id: str,
            context_id: str,
            emit: Callable[[dict[str, Any]], Awaitable[None]],
        ) -> str:
            """Run the turn, streaming its events; the task's terminal state."""
            task = await self.storage.load_task(task_id)
            if task is None:
                raise ValueError(f"Task {task_id} not found")
            if task["status"]["state"] != "submitted":
                raise ValueError(
                    f"Task {task_id} has already been processed "
                    f"(state: {task['status']['state']})"
                )
            await self.storage.update_task(task_id, state="working")
            history = await self.storage.load_context(context_id) or []
            history.extend(self.build_message_history(task.get("history", [])))
            try:
                async with self.agent.run_stream_events(
                    message_history=history
                ) as events:
                    async for event in events:
                        await self._emit_stream_event(task_id, context_id, event, emit)
                result = events.result
            except Exception:
                # The agent turn failed; the store records it, the gate emits
                # the final-state event from the returned state.
                await self.storage.update_task(task_id, state="failed")
                return "failed"
            a2a_messages: list[Any] = []
            for message in result.new_messages():
                if isinstance(message, ModelRequest):
                    continue
                parts = self._response_parts_to_a2a(message.parts)
                if parts:
                    a2a_messages.append(
                        Message(
                            role="agent",
                            parts=parts,
                            kind="message",
                            message_id=str(uuid.uuid4()),
                        )
                    )
            artifacts = self.build_artifacts(result.output)
            await self.storage.update_task(
                task_id,
                state="completed",
                new_artifacts=artifacts,
                new_messages=a2a_messages,
            )
            return "completed"

        async def _emit_stream_event(
            self,
            task_id: str,
            context_id: str,
            event: Any,
            emit: Callable[[dict[str, Any]], Awaitable[None]],
        ) -> None:
            if isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, TextPartDelta) and delta.content_delta:
                    await emit(
                        {
                            "kind": "text-delta",
                            "taskId": task_id,
                            "contextId": context_id,
                            "text": delta.content_delta,
                        }
                    )
            elif isinstance(event, FunctionToolCallEvent):
                await emit(
                    {
                        "kind": "tool-call",
                        "taskId": task_id,
                        "contextId": context_id,
                        "toolName": event.part.tool_name,
                        "arguments": to_jsonable_python(event.part.args or {}),
                    }
                )
            elif isinstance(event, FunctionToolResultEvent):
                part = event.part
                if isinstance(part, ToolReturnPart):
                    await emit(
                        {
                            "kind": "tool-result",
                            "taskId": task_id,
                            "contextId": context_id,
                            "toolName": part.tool_name,
                            "result": to_jsonable_python(part.content),
                        }
                    )

    return _TurnWorker(agent=agent, broker=broker, storage=storage)


class _ProtocolGate:
    """Serves the SDK-owned protocol methods on ``/`` in front of the app.

    ``tasks/list`` pages the storage's caller contexts; ``message/stream``
    runs the turn as a durable background job, streaming its events to the
    caller and to the storage's turn log; ``tasks/resubscribe`` replays
    that log after the caller's cursor. Every other request reaches the A2A
    application unchanged; the inner app's task manager stays reachable for
    hosts and tests.
    """

    def __init__(self, inner: Any, storage: Any, worker: Any) -> None:
        self._inner = inner
        self._storage = storage
        self._worker = worker
        self._turn_queues: dict[str, asyncio.Queue[Any]] = {}
        self._turn_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def task_manager(self) -> Any:
        """The inner application's task manager."""
        return self._inner.task_manager

    @asynccontextmanager
    async def lifespan(self, app: Any) -> AsyncIterator[None]:
        """Cancel in-flight streamed turns when the host shuts the app down."""
        yield
        for task in self._turn_tasks.values():
            task.cancel()
        if self._turn_tasks:
            await asyncio.gather(*self._turn_tasks.values(), return_exceptions=True)

    def _deliver(
        self, task_id: str, context_id: str, queue: asyncio.Queue[Any]
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """Append one wire event to the durable log first, then to the live stream."""

        async def deliver(event: dict[str, Any]) -> None:
            seq = await self._storage.append_turn_event(task_id, event)
            await queue.put({**event, "seq": seq})

        return deliver

    async def _run_turn(
        self, task_id: str, context_id: str, queue: asyncio.Queue[Any]
    ) -> None:
        """Run one streamed turn to completion, past a dropped client.

        Every produced event lands in the durable log before it reaches
        the queue, so the tail after the caller's last received sequence
        is always replayable. The queue ends with a ``None`` sentinel; the
        final-state event is present unless the durable log itself failed.
        """
        state = "failed"
        try:
            state = await self._worker.stream_turn(
                task_id, context_id, self._deliver(task_id, context_id, queue)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The turn could not run (storage error); report it as failed.
            state = "failed"
        with suppress(Exception):
            await self._deliver(task_id, context_id, queue)(
                {
                    "kind": "final-state",
                    "taskId": task_id,
                    "contextId": context_id,
                    "state": state,
                }
            )
        await queue.put(None)
        self._turn_queues.pop(task_id, None)
        self._turn_tasks.pop(task_id, None)

    async def _stream_to_client(
        self,
        queue: asyncio.Queue[Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Send queued turn events as SSE frames until the final state or the client drops."""
        disconnect = asyncio.Event()

        async def wait_disconnect() -> None:
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    disconnect.set()
                    return

        getter: asyncio.Task[Any] | None = None
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                waiter = asyncio.create_task(disconnect.wait())
                done, _ = await asyncio.wait(
                    {getter, waiter}, return_when=asyncio.FIRST_COMPLETED
                )
                waiter.cancel()
                if getter not in done:
                    getter.cancel()
                    break
                event = getter.result()
                if event is None:
                    break
                await _sse_frame(send, event)
                if event.get("kind") == "final-state":
                    break
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
            await _sse_end(send)

    async def _stream_message(
        self,
        request: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        request_id = request.get("id")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}
        message = params.get("message")
        parts = message.get("parts") if isinstance(message, dict) else None
        context_id = message.get("contextId") if isinstance(message, dict) else None
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("role"), str)
            or not isinstance(parts, list)
            or not parts
            or any(not isinstance(part, dict) for part in parts)
            or (
                context_id is not None
                and (
                    not isinstance(context_id, str)
                    or len(context_id) > _MAX_CONTEXT_ID_LEN
                )
            )
        ):
            await _send_json(
                send, _error_reply(request_id, _INVALID_PARAMS, "Invalid params")
            )
            return
        if not context_id:
            context_id = str(uuid.uuid4())
        try:
            task = await self._storage.submit_task(context_id, message)
        except Exception:
            # The durable log is unavailable; the turn must not run untracked.
            await _send_json(
                send, _error_reply(request_id, _INTERNAL, "internal error")
            )
            return
        task_id = task.get("id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id:
            await _send_json(
                send, _error_reply(request_id, _INTERNAL, "internal error")
            )
            return
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._turn_queues[task_id] = queue
        self._turn_tasks[task_id] = asyncio.create_task(
            self._run_turn(task_id, context_id, queue)
        )
        await _sse_start(send)
        await self._stream_to_client(queue, receive, send)

    async def _resubscribe(
        self,
        request: dict[str, Any],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        request_id = request.get("id")
        params = request.get("params")
        if not isinstance(params, dict):
            params = {}
        task_id = params.get("id")
        caller = params.get("caller")
        after_seq = params.get("afterSeq", 0)
        if (
            not isinstance(task_id, str)
            or not task_id
            or len(task_id) > _MAX_TASK_ID_LEN
            or not isinstance(caller, str)
            or not caller
            or len(caller) > _MAX_CALLER_LEN
            or isinstance(after_seq, bool)
            or not isinstance(after_seq, int)
            or after_seq < 0
        ):
            await _send_json(
                send, _error_reply(request_id, _INVALID_PARAMS, "Invalid params")
            )
            return
        try:
            events = await self._storage.turn_events_after(caller, task_id, after_seq)
        except ForeignContextError:
            # The task or cursor does not belong to this caller: no data.
            await _send_json(
                send, _error_reply(request_id, _FOREIGN_CONTEXT, "foreign context")
            )
            return
        await _sse_start(send)
        for event in events:
            if isinstance(event, dict):
                await _sse_frame(send, event)
        await _sse_end(send)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/"
        ):
            await self._inner(scope, receive, send)
            return
        body = await _read_body(receive)
        request = _json_object(body)
        if not isinstance(request, dict):
            await self._inner(scope, _replay_body(body), send)
            return
        method = request.get("method")
        if method == "tasks/list":
            await _send_json(send, await _tasks_list_reply(self._storage, request))
            return
        if method == "message/stream":
            await self._stream_message(request, receive, send)
            return
        if method == "tasks/resubscribe":
            await self._resubscribe(request, send)
            return
        await self._inner(scope, _replay_body(body), send)


def streaming_wired(storage: Any) -> bool:
    """Whether the wired storage holds the turn-event log that ``message/stream`` needs.

    The agent card advertises ``streaming`` from this and from nothing else: the
    protocol gate serves ``message/stream`` and ``tasks/resubscribe`` only over
    that log.
    """
    return isinstance(storage, Storage)


def require_a2a_extra(feature: str) -> None:
    """Raise ``ImportError`` naming the ``a2a`` extra when it is absent."""
    missing = []
    for name in ("fasta2a", "starlette"):
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        raise ImportError(
            f"{feature} needs {' and '.join(missing)}, which the base install of "
            'scion-sdk leaves out; install "scion-sdk[a2a]"'
        )


def create_a2a_app(
    agent: Any,
    *,
    name: str,
    url: str,
    version: str,
    description: str = "",
    skills: list[AgentSkill] | None = None,
    storage: Any,
    broker: Any,
) -> Any:
    """Return the A2A application for ``agent`` with the given card fields.

    The returned app serves the A2A protocol methods plus the SDK-owned
    ones: ``tasks/list`` from ``storage.list_contexts``,
    ``message/stream`` (the turn's events as server-sent events, appended
    to the storage's turn log as they are produced), and
    ``tasks/resubscribe`` (the durable log replayed after the caller's
    ``afterSeq`` cursor). A storage without those methods is refused at
    construction.
    """
    require_a2a_extra("create_a2a_app")
    if not isinstance(storage, Storage):
        raise TypeError(
            "storage must implement list_contexts(caller, cursor, limit), "
            "append_turn_event(task_id, event) and "
            "turn_events_after(caller, task_id, after_seq) alongside the "
            "fasta2a Storage interface"
        )
    from fasta2a import FastA2A, Skill
    from starlette.types import ASGIApp  # noqa: F401 - the extra's contract

    worker = _build_turn_worker(agent, broker, storage)
    card_skills = [
        Skill(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=list(skill.tags),
            input_modes=list(skill.input_modes),
            output_modes=list(skill.output_modes),
        )
        for skill in skills or []
    ]

    async def lifespan(
        app: Any,
    ) -> AsyncIterator[None]:  # pragma: no cover - exercised by the host
        async with app.task_manager, worker.run(), gate.lifespan(app):
            yield

    class _DerivedCardApp(FastA2A):  # type: ignore[misc]
        """The fasta2a app whose card derives ``streaming`` from the wired storage.

        fasta2a hardcodes ``streaming: false``; the gate serves streaming, so the
        card reports what is wired.
        """

        async def _agent_card_endpoint(self, request: Any) -> Any:
            from starlette.responses import Response

            card = json.loads(bytes((await super()._agent_card_endpoint(request)).body))
            card.setdefault("capabilities", {})["streaming"] = streaming_wired(storage)
            return Response(
                json.dumps(card).encode("utf-8"), media_type="application/json"
            )

    app = _DerivedCardApp(
        storage=storage,
        broker=broker,
        name=name,
        url=url,
        version=version,
        description=description or None,
        skills=card_skills,
        lifespan=asynccontextmanager(lifespan),
    )
    gate = _ProtocolGate(app, storage, worker)
    return gate
