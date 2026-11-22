"""Server-side A2A application factory (needs the ``a2a`` extra).

CONCEPT:SCION-CONNECTOR-CONTROL-R014.21

Builds a fasta2a ASGI app for a pydantic-ai agent. The caller supplies the
broker and a durable :class:`Storage`; this SDK holds no task store, and an
in-memory storage is a test double only. Besides the protocol methods, the
app serves ``tasks/list`` from the storage's ``list_contexts``: one page of
the caller's contexts with a continuation cursor, and ``-32001`` when the
caller names a context or a cursor it does not own. A host mounts the
returned app behind its own admission and authentication.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from .models import AgentSkill

__all__ = [
    "ForeignContextError",
    "Storage",
    "create_a2a_app",
    "require_a2a_extra",
]

#: JSON-RPC code for a caller naming a context or cursor it does not own.
_FOREIGN_CONTEXT = -32001
_INVALID_PARAMS = -32602
_DEFAULT_PAGE = 50
_MAX_PAGE = 100
_MAX_CALLER_LEN = 256
_MAX_CURSOR_LEN = 1024
_MAX_CONTEXT_ID_LEN = 1024


class ForeignContextError(ValueError):
    """The caller named a context or cursor that does not belong to it."""


@runtime_checkable
class Storage(Protocol):
    """The durable storage's caller-scoped page, on top of the fasta2a
    ``Storage`` interface the application itself requires.

    ``create_a2a_app`` serves ``tasks/list`` from :meth:`list_contexts`.
    An in-memory implementation is a test double only; production callers
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


class _TasksListGate:
    """Serves JSON-RPC ``tasks/list`` on ``/`` from the storage.

    Every other request reaches the A2A application unchanged; the inner
    app's task manager stays reachable for hosts and tests.
    """

    def __init__(self, inner: Any, storage: Any) -> None:
        self._inner = inner
        self._storage = storage

    @property
    def task_manager(self) -> Any:
        """The inner application's task manager."""
        return self._inner.task_manager

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
        if not (isinstance(request, dict) and request.get("method") == "tasks/list"):
            await self._inner(scope, _replay_body(body), send)
            return
        await _send_json(send, await _tasks_list_reply(self._storage, request))


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

    The returned app serves the A2A protocol methods and ``tasks/list``
    from ``storage.list_contexts``; a storage without that method is
    refused at construction.
    """
    require_a2a_extra("create_a2a_app")
    if not isinstance(storage, Storage):
        raise TypeError(
            "storage must implement list_contexts(caller, cursor, limit) "
            "alongside the fasta2a Storage interface"
        )
    from fasta2a import FastA2A, Skill
    from fasta2a.pydantic_ai import AgentWorker
    from starlette.types import ASGIApp  # noqa: F401 - the extra's contract

    worker = AgentWorker(agent=agent, broker=broker, storage=storage)
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
        async with app.task_manager, worker.run():
            yield

    from contextlib import asynccontextmanager

    app = FastA2A(
        storage=storage,
        broker=broker,
        name=name,
        url=url,
        version=version,
        description=description or None,
        skills=card_skills,
        lifespan=asynccontextmanager(lifespan),
    )
    return _TasksListGate(app, storage)
