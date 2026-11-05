"""Backends for the configured sinks (SCION-CENTRALIZED-CAPABILITIES-R042.8).

The exchange builds each store's backend once with
:func:`scion_sdk.storage.create_backend` and reuses it for every run. No other
exchange module builds a storage client.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from scion_sdk.storage.factory import create_backend

if TYPE_CHECKING:
    from scion_sdk.credentials.resolver import CredentialResolver
    from scion_sdk.exchange.config import ExchangeSettings
    from scion_sdk.storage.base import ObjectStoreBackend

__all__ = ["SinkBackends"]


class SinkBackends:
    """One cached backend per store name."""

    def __init__(
        self, settings: ExchangeSettings, resolver: CredentialResolver | None = None
    ) -> None:
        self._settings = settings
        self._resolver = resolver
        self._cache: dict[str, ObjectStoreBackend] = {}
        self._lock = threading.Lock()

    def backend(self, store: str) -> ObjectStoreBackend:
        """The backend of ``store``, built on first use."""
        with self._lock:
            cached = self._cache.get(store)
            if cached is None:
                cached = create_backend(
                    self._settings.stores[store], resolver=self._resolver
                )
                self._cache[store] = cached
            return cached

    def put(self, store: str, backend: ObjectStoreBackend) -> None:
        """Install a pre-built backend (an in-process double in tests)."""
        with self._lock:
            self._cache[store] = backend
