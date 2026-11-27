"""Read the pinned digests of a connector release from the engine's release record.

The release pipeline records every connector release through the engine's
``RecordRelease`` method: ``repo`` is the distribution name and the sealed
record carries ``artifact_digests`` (SCION-CONNECTOR-CONTROL-R650.1.4). The
record is create-only, so a pin cannot be replaced after release.
"""

from __future__ import annotations

import json
from typing import Any

from scion_sdk.ports.release_pins import ReleasePins

__all__ = ["MedullaReleasePins", "release_node_id"]


def release_node_id(distribution: str, version: str) -> str:
    """The graph node id of a release record, as the engine derives it."""
    return f"release:{distribution}:{version}"


def _properties(result: Any) -> dict[str, Any]:
    body = getattr(result, "payload", result)
    return body if isinstance(body, dict) else {}


def _digests(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return frozenset()
    if not isinstance(value, (list, tuple)):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str))


class MedullaReleasePins(ReleasePins):
    """Pins read from the sealed ``Release`` node of one engine graph."""

    def __init__(self, client: Any, graph: str | None = None) -> None:
        self._client = client
        self._graph = graph

    async def pinned_digests(self, distribution: str, version: str) -> frozenset[str]:
        from medulla.generated.graph import send_get_node_properties

        node_id = release_node_id(distribution, version)
        try:
            result = await send_get_node_properties(
                self._client, {"node_id": node_id}, self._graph
            )
        except KeyError:
            return frozenset()
        properties = _properties(result)
        if properties.get("type") != "Release":
            return frozenset()
        return _digests(properties.get("artifact_digests"))
