"""Topic-based message streaming over WebSocket.

:class:`StreamBus` fans published :class:`StreamMessage` objects out to
subscribers by topic; :class:`WebSocketStreamAdapter` feeds a bus from a
WebSocket source. The ``stream`` extra supplies the WebSocket client.
:func:`transition` is the one function that owns the stream state machine
(SCION-REPOSITORY-TRANSPORT-R008).
"""

from __future__ import annotations

from scion_sdk.streaming._bus import StreamBus
from scion_sdk.streaming._types import (
    CallbackSubscriber,
    StreamMessage,
    StreamSubscriber,
)
from scion_sdk.streaming._websocket import (
    STREAM_EDGES,
    StreamEvent,
    StreamState,
    StreamTransitionRefused,
    WebSocketStreamAdapter,
)
from scion_sdk.streaming._websocket import transition as stream_transition

__all__ = [
    "STREAM_EDGES",
    "CallbackSubscriber",
    "StreamBus",
    "StreamEvent",
    "StreamMessage",
    "StreamState",
    "StreamSubscriber",
    "StreamTransitionRefused",
    "WebSocketStreamAdapter",
    "stream_transition",
]
