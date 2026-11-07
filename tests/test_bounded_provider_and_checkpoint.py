"""The shared bounded-provider policy and generation-checkpoint check, by their public names."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from scion_sdk.http.bounded_provider import (
    BoundedProvider,
    bounded_bytes,
    fetch_bounded,
)
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.ingest.stream_checkpoint import authorize_generation


@asynccontextmanager
async def _quota(_provider: str) -> AsyncIterator[None]:
    yield


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")
def test_bounded_provider_refuses_an_unpinned_retried_or_unbounded_policy() -> None:
    pinned = HttpClientOptions(
        base_url="https://api.example.test",
        pin_egress=True,
        retry=RetryPolicy(max_attempts=1),
    )
    provider = BoundedProvider(provider="demo", http=pinned, quota=_quota, max_bytes=8)
    assert (provider.provider, provider.max_bytes) == ("demo", 8)
    for http, max_bytes in (
        (HttpClientOptions(base_url="https://api.example.test"), 8),
        (pinned, 0),
    ):
        with pytest.raises(ValueError, match="invalid bounded provider policy"):
            BoundedProvider(
                provider="demo", http=http, quota=_quota, max_bytes=max_bytes
            )
    assert inspect.iscoroutinefunction(fetch_bounded)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")
async def test_bounded_bytes_joins_chunks_and_refuses_past_the_limit() -> None:
    assert await bounded_bytes(_chunks(b"ab", b"cd"), 4) == b"abcd"
    with pytest.raises(ValueError, match="exceeds admitted byte bound"):
        await bounded_bytes(_chunks(b"ab", b"cde"), 4)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R044")
async def test_authorize_generation_refuses_before_reading_a_foreign_checkpoint() -> (
    None
):
    async def allow(_tenant: str, _context: Any) -> bool:
        return True

    async def deny(_tenant: str, _context: Any) -> bool:
        return False

    good = {"binding": "b1", "generation": "g1"}
    args = {"tenant": "t", "context": None, "binding": "b1"}
    await authorize_generation(allow, mode="delta", state=good, **args)
    await authorize_generation(allow, mode="delta", state=None, **args)
    for authorize, mode in ((deny, "delta"), (allow, "full")):
        with pytest.raises(ValueError, match="access refused"):
            await authorize_generation(authorize, mode=mode, state=good, **args)
    for state in ({"binding": "b2", "generation": "g1"}, {"binding": "b1"}):
        with pytest.raises(ValueError, match="invalid source checkpoint"):
            await authorize_generation(allow, mode="delta", state=state, **args)
