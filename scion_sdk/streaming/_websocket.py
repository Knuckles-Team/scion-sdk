"""WebSocket feed into a :class:`StreamBus`.

:class:`WebSocketStreamAdapter` is one explicit state machine
(SCION-REPOSITORY-TRANSPORT-R008). :func:`transition` owns every edge of
:data:`STREAM_EDGES`; any other pair raises :class:`StreamTransitionRefused`.
``WebSocketStreamAdapter.state`` is the one stored field and ``is_connected``
is derived from it. The retry count is data, not state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from scion_sdk.ingest.backoff import Backoff
from scion_sdk.streaming._bus import StreamBus
from scion_sdk.streaming._types import StreamMessage
from scion_sdk.transition_refused import MachineTransitionRefused

if TYPE_CHECKING:
    from scion_sdk.connector_lifecycle import CycleHealth

logger = logging.getLogger(__name__)

_RETRY_INITIAL_SECONDS = 1.0
_RETRY_MAXIMUM_SECONDS = 60.0


class StreamState(StrEnum):
    """The closed states of a stream adapter; ``Closed`` is terminal."""

    IDLE = "Idle"
    CONNECTING = "Connecting"
    OPEN = "Open"
    CLOSING = "Closing"
    CLOSED = "Closed"
    FAILED = "Failed"


class StreamEvent(StrEnum):
    """The closed events of a stream adapter."""

    CONNECT = "connect"
    OPENED = "opened"
    CLOSE = "close"
    CLOSED = "closed"
    ERROR = "error"
    RETRY = "retry"


class StreamEdge(NamedTuple):
    """One legal edge of the stream table."""

    id: str
    source: StreamState
    event: StreamEvent
    target: StreamState


_T = StreamState
_V = StreamEvent
#: The edge table of SCION-REPOSITORY-TRANSPORT-R008.1. Every other pair is refused.
STREAM_EDGES: tuple[StreamEdge, ...] = (
    StreamEdge("idle-connect", _T.IDLE, _V.CONNECT, _T.CONNECTING),
    StreamEdge("idle-close", _T.IDLE, _V.CLOSE, _T.CLOSED),
    StreamEdge("connecting-opened", _T.CONNECTING, _V.OPENED, _T.OPEN),
    StreamEdge("connecting-error", _T.CONNECTING, _V.ERROR, _T.FAILED),
    StreamEdge("connecting-close", _T.CONNECTING, _V.CLOSE, _T.CLOSING),
    StreamEdge("open-close", _T.OPEN, _V.CLOSE, _T.CLOSING),
    StreamEdge("open-error", _T.OPEN, _V.ERROR, _T.FAILED),
    StreamEdge("closing-closed", _T.CLOSING, _V.CLOSED, _T.CLOSED),
    StreamEdge("closing-error", _T.CLOSING, _V.ERROR, _T.CLOSED),
    StreamEdge("failed-retry", _T.FAILED, _V.RETRY, _T.CONNECTING),
    StreamEdge("failed-close", _T.FAILED, _V.CLOSE, _T.CLOSED),
)
_TARGETS = {(edge.source, edge.event): edge.target for edge in STREAM_EDGES}


class StreamTransitionRefused(MachineTransitionRefused):
    """A stream event the table does not declare in the current state (R008.2).

    Carries ``machine`` (``WebSocketStream``), ``from_state`` and ``event``; its
    code is ``STREAM_TRANSITION_REFUSED``. It maps to the core
    ``TransitionRefused`` class.
    """

    code: ClassVar[str] = "STREAM_TRANSITION_REFUSED"
    machine_name: ClassVar[str] = "WebSocketStream"


def transition(state: StreamState, event: StreamEvent | str) -> StreamState:
    """The one function that owns every stream edge.

    ``event`` may also be the name of an operation that needs a state, such as
    ``listen``; such a name has no edge and is always refused.

    Raises:
        StreamTransitionRefused: the pair is not an edge of :data:`STREAM_EDGES`.
    """
    try:
        return _TARGETS[(state, StreamEvent(event))]
    except (KeyError, ValueError):
        raise StreamTransitionRefused(state.value, str(event)) from None


class WebSocketStreamAdapter:
    """Feeds a :class:`StreamBus` from a WebSocket source.

    ``connect`` opens the connection (default: ``websockets.connect`` from the
    ``stream`` extra). Each JSON text frame becomes a published
    :class:`StreamMessage`; a frame that is not JSON is skipped.

    ``state`` is the one stored field. When ``health`` and ``connector`` are
    given, reaching ``Failed`` sends ``cycle_fail`` and reaching ``Open`` sends
    ``cycle_ok`` to the connector lifecycle of SCION-CONNECTOR-CONTROL-R917.
    ``retry`` waits the delay of ``backoff`` before it reopens.
    """

    def __init__(
        self,
        bus: StreamBus,
        url: str = "",
        connect: Callable[[str], Any] | None = None,
        *,
        health: CycleHealth | None = None,
        connector: str = "",
        backoff: Backoff | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.bus = bus
        self.url = url
        self._connect = connect
        self.state: StreamState = StreamState.IDLE
        self.retries = 0
        self._connection: Any = None
        self._health = health
        self._connector = connector
        self._backoff = backoff or Backoff(
            _RETRY_INITIAL_SECONDS, _RETRY_MAXIMUM_SECONDS
        )
        self._sleep = sleep

    def _move(self, event: StreamEvent | str) -> StreamState:
        """Apply ``event`` through :func:`transition`, then drop or report."""
        self.state = transition(self.state, event)
        if self.state not in {
            StreamState.CONNECTING,
            StreamState.OPEN,
            StreamState.CLOSING,
        }:
            self._connection = None
        self._report()
        return self.state

    def _report(self) -> None:
        health = self._health
        if health is None:
            return
        if self.state is StreamState.FAILED:
            health.record_cycle_failure(self._connector, next_retry_seconds=None)
        elif self.state is StreamState.OPEN:
            health.record_cycle_success(self._connector)

    async def connect(self) -> bool:
        """Open the connection; ``False`` when the client or the peer is unavailable.

        Raises:
            StreamTransitionRefused: the adapter is not ``Idle``; no connection
                is opened.
        """
        self._move(StreamEvent.CONNECT)
        return await self._open()

    async def retry(self) -> bool:
        """Reopen a ``Failed`` adapter after the next backoff delay.

        Raises:
            StreamTransitionRefused: the adapter is not ``Failed``.
        """
        transition(self.state, StreamEvent.RETRY)
        await self._sleep(self._backoff.next_delay())
        self.retries += 1
        self._move(StreamEvent.RETRY)
        return await self._open()

    async def _open(self) -> bool:
        opener = self._connect
        if opener is None:
            try:
                import websockets
            except ImportError:
                logger.error("WebSocket streaming needs scion-sdk[stream]")
                self._move(StreamEvent.ERROR)
                return False
            opener = websockets.connect
        try:
            self._connection = await opener(self.url)
        except Exception as exc:
            logger.error("WebSocket connection failed (%s)", type(exc).__name__)
            self._move(StreamEvent.ERROR)
            return False
        self._move(StreamEvent.OPENED)
        self._backoff.reset()
        return True

    async def disconnect(self) -> None:
        """Close the connection and reach ``Closed``.

        Raises:
            StreamTransitionRefused: the adapter is already ``Closing`` or ``Closed``.
        """
        was_idle_or_failed = self.state in {StreamState.IDLE, StreamState.FAILED}
        connection = self._connection
        self._move(StreamEvent.CLOSE)
        if was_idle_or_failed:
            return
        try:
            if connection is not None:
                await connection.close()
        except Exception as exc:
            logger.error("WebSocket close failed (%s)", type(exc).__name__)
            self._move(StreamEvent.ERROR)
            return
        self._move(StreamEvent.CLOSED)

    async def listen(self, topic_prefix: str = "") -> None:
        """Publish every incoming JSON frame, under ``topic_prefix`` when given.

        Raises:
            StreamTransitionRefused: the adapter is not ``Open``.
        """
        if self.state is not StreamState.OPEN:
            raise StreamTransitionRefused(self.state.value, "listen")
        try:
            async for raw_message in self._connection:
                await self._publish_frame(raw_message, topic_prefix)
        except Exception as exc:
            logger.error("WebSocket listen ended (%s)", type(exc).__name__)
            self._move(StreamEvent.ERROR)

    async def _publish_frame(self, raw_message: str, topic_prefix: str) -> None:
        try:
            msg = StreamMessage.from_json(raw_message)
        except json.JSONDecodeError:
            logger.warning("skipped a WebSocket frame that is not JSON")
            return
        if topic_prefix:
            msg.topic = ".".join((topic_prefix, msg.topic))
        await self.bus.publish(msg)

    @property
    def is_connected(self) -> bool:
        """Whether the connection is open; derived from ``state``."""
        return self.state is StreamState.OPEN
