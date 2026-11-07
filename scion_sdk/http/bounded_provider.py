"""Shared egress and size policy for host-admitted source providers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from scion_sdk.http.client import build_http_client
from scion_sdk.http.options import HttpClientOptions

__all__ = ["BoundedProvider", "bounded_bytes", "fetch_bounded"]


class BoundedProvider[T: str]:
    """A provider whose egress is pinned, unretried and capped at ``max_bytes``."""

    def __init__(
        self,
        *,
        provider: T,
        http: HttpClientOptions,
        quota: Callable[[T], AbstractAsyncContextManager[Any]],
        max_bytes: int,
    ) -> None:
        if not http.pin_egress or http.retry.max_attempts != 1 or max_bytes < 1:
            raise ValueError("invalid bounded provider policy")
        self.provider = provider
        self.http = http
        self.quota = quota
        self.max_bytes = max_bytes


async def bounded_bytes(chunks: AsyncIterator[bytes], limit: int) -> bytes:
    """Join ``chunks``; refuse once they exceed ``limit`` bytes."""
    result = bytearray()
    async for chunk in chunks:
        if len(result) + len(chunk) > limit:
            raise ValueError("provider response exceeds admitted byte bound")
        result.extend(chunk)
    return bytes(result)


async def fetch_bounded(http: HttpClientOptions, path: str, max_bytes: int) -> bytes:
    """GET ``path`` through a governed client; refuse a body over ``max_bytes``."""
    async with (
        build_http_client(http, async_=True) as client,
        client.stream("GET", path) as response,
    ):
        response.raise_for_status()
        return await bounded_bytes(response.aiter_bytes(), max_bytes)
