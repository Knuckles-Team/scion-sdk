"""List a connector server's tools through the ``Transport`` port, bounded in time.

Certification opens a session, calls ``tools/list`` and reads ``connector://resources``: it never calls a
tool, so it never reaches the connector's upstream API.

A connector with an upstream registry (SCION-CONNECTOR-CONTROL-R914) serves
proxied tools that change with their upstream release, so none of them is
pinned: the listing splits them out by the ``scion/upstream`` tool metadata,
and the registry's ``connector://upstreams`` resource is read so the report
can name each upstream with its fingerprint. Certification starts the
connector with no activation, so a lazy registry lists no proxied tool and
starts no upstream process.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anyio

from scion_sdk.manifest.resource_descriptor import RESOURCES_URI
from scion_sdk.manifest.tool_schema import read_field
from scion_sdk.ports.session import McpSession, TransportEndpoint
from scion_sdk.ports.transport import Transport
from scion_sdk.upstream_wire import UPSTREAM_META_KEY, UPSTREAMS_RESOURCE_URI

__all__ = [
    "ToolListing",
    "ToolListingError",
    "UpstreamReport",
    "list_server_tools",
    "split_upstream_tools",
    "upstream_reports",
]


class ToolListingError(RuntimeError):
    """The server could not be started or reached, or did not answer ``tools/list``."""


@dataclass(frozen=True)
class ToolListing:
    """What a server reported: its identity, its tools and its declaration.

    ``tools`` holds the server's own tools only; proxied upstream tools,
    excluded from every pin, come back in ``upstream_tools``. ``upstreams_body``
    is the ``connector://upstreams`` body, ``""`` when the server serves no
    registry.
    """

    server_name: str
    server_version: str
    tools: tuple[Any, ...]
    upstream_tools: tuple[Any, ...] = ()
    resources: str = ""
    upstreams_body: str = ""


@dataclass(frozen=True)
class UpstreamReport:
    """One declared upstream, as certification names it in the report (R914.12).

    ``fingerprint`` and ``version`` come from the registry's last successful
    listing and are ``None`` while the namespace is inactive;
    ``expected_fingerprint`` is the declaration's pin, known without listing.
    """

    name: str
    namespace: str
    kind: str
    state: str
    tool_count: int = 0
    fingerprint: str | None = None
    version: str | None = None
    expected_fingerprint: str | None = None


def split_upstream_tools(
    tools: Sequence[Any],
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """``(local, proxied)``: a tool is proxied when its meta carries the marker.

    The filter is keyed only on the ``scion/upstream`` metadata
    (SCION-CONNECTOR-CONTROL-R914.12); a local tool is pinned exactly as
    before, a proxied tool never.
    """
    local: list[Any] = []
    proxied: list[Any] = []
    for tool in tools:
        meta = read_field(tool, "meta")
        if isinstance(meta, Mapping) and UPSTREAM_META_KEY in meta:
            proxied.append(tool)
        else:
            local.append(tool)
    return tuple(local), tuple(proxied)


def upstream_reports(body: str) -> tuple[UpstreamReport, ...]:
    """Parse a ``connector://upstreams`` body into the report's entries.

    Raises:
        ToolListingError: the body is not the registry's JSON document. The
            block is served by the same SDK that reads it, so a malformed body
            is a connector defect, not a missing registry.
    """
    if not body:
        return ()
    try:
        document = json.loads(body)
        entries = document["upstreams"]
        if not isinstance(entries, list):
            raise ValueError
        reports = [
            UpstreamReport(
                name=str(entry["name"]),
                namespace=str(entry.get("namespace", "")),
                kind=str(entry.get("kind", "")),
                state=str(entry.get("state", "")),
                tool_count=int(entry.get("tool_count", 0) or 0),
                fingerprint=entry.get("fingerprint"),
                version=entry.get("version"),
                expected_fingerprint=entry.get("expected_fingerprint"),
            )
            for entry in entries
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolListingError("connector://upstreams holds a malformed body") from exc
    return tuple(reports)


async def _upstreams_body(session: McpSession) -> str:
    """The ``connector://upstreams`` body, or ``""`` when no registry serves it."""
    for resource in await session.list_resources():
        if read_field(resource, "uri") == UPSTREAMS_RESOURCE_URI:
            return await session.read_resource(UPSTREAMS_RESOURCE_URI)
    return ""


async def _listing(transport: Transport, endpoint: TransportEndpoint) -> ToolListing:
    async with transport.session(endpoint) as session:
        identity = await session.server_identity()
        tools = await session.list_tools()
        resources = await session.read_resource(RESOURCES_URI)
        upstreams_body = await _upstreams_body(session)
    local, proxied = split_upstream_tools(tools)
    return ToolListing(
        identity.name,
        identity.version,
        local,
        proxied,
        resources,
        upstreams_body,
    )


def _bounded_timeout(timeout_seconds: float) -> float:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    return timeout_seconds


async def list_server_tools(
    transport: Transport, endpoint: TransportEndpoint, *, timeout_seconds: float
) -> ToolListing:
    """Open a session, list the tools and close it within ``timeout_seconds``.

    Raises:
        ValueError: ``timeout_seconds`` is not finite and positive.
        ToolListingError: the server did not start, answer or list its tools in
            time. Its public message never includes the underlying exception.
    """
    timeout = _bounded_timeout(timeout_seconds)
    try:
        with anyio.fail_after(timeout):
            return await _listing(transport, endpoint)
    except Exception as exc:
        raise ToolListingError("the connector server did not list its tools") from exc
