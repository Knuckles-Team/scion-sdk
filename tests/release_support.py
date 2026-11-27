"""Pinned-digest fixtures shared by the runner tests (SCION-CONNECTOR-CONTROL-R650.1)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import SimpleNamespace
from typing import Any

from scion_sdk.manifest.release_digest import ReleaseEvidence
from scion_sdk.ports.session import McpSession, TransportEndpoint

RELEASE_URI = "connector://release"
DISTRIBUTION = "fixture-agent"
VERSION = "1.0.0"
CONTENT = "ab" * 32
EVIDENCE = ReleaseEvidence(DISTRIBUTION, VERSION, CONTENT)


class FakePins:
    """Release pins held in memory, keyed by distribution and version."""

    def __init__(self, *entries: tuple[str, str, frozenset[str]]) -> None:
        self.entries = {(d, v): digests for d, v, digests in entries}
        self.reads = 0

    async def pinned_digests(self, distribution: str, version: str) -> frozenset[str]:
        self.reads += 1
        return self.entries.get((distribution, version), frozenset())


PINNED = FakePins((DISTRIBUTION, VERSION, frozenset({EVIDENCE.pin})))


class ReportingSession:
    """Serves ``connector://release`` for whatever the inner session serves."""

    def __init__(self, inner: McpSession, evidence: ReleaseEvidence) -> None:
        self._inner = inner
        self._evidence = evidence

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def watch(self, resource_uris: Any) -> bool:
        return await self._inner.watch(resource_uris)  # type: ignore[attr-defined,no-any-return]

    async def next_changes(self) -> Any:
        return await self._inner.next_changes()  # type: ignore[attr-defined]

    async def list_resources(self) -> list[Any]:
        found = list(await self._inner.list_resources())
        found.append(SimpleNamespace(uri=RELEASE_URI, name=RELEASE_URI))
        return found

    async def read_resource(self, uri: str) -> str:
        if uri == RELEASE_URI:
            return json.dumps(
                {
                    "distribution": self._evidence.distribution,
                    "version": self._evidence.version,
                    "content_sha256": self._evidence.content_sha256,
                }
            )
        return await self._inner.read_resource(uri)


class ReportingTransport:
    """A transport whose sessions report the fixture's pinned content digest."""

    name = "reporting"

    def __init__(self, inner: Any, evidence: ReleaseEvidence = EVIDENCE) -> None:
        self._inner = inner
        self._evidence = evidence

    def session(
        self, endpoint: TransportEndpoint
    ) -> AbstractAsyncContextManager[McpSession]:
        @asynccontextmanager
        async def open_session() -> AsyncIterator[McpSession]:
            async with self._inner.session(endpoint) as session:
                yield ReportingSession(session, self._evidence)  # type: ignore[misc]

        return open_session()
