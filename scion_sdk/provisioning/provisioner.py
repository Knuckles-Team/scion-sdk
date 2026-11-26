"""SDK provisioner: allocate and reconcile instances with actual receipts.

SCION-CONNECTOR-CONTROL-R906. ``allocate`` returns a receipt that names the
real instance ids and a digest, fenced by the idempotency key. When the adapter
cannot say whether the allocation happened, ``allocate`` raises
``ALLOCATION_UNKNOWN``; ``reconcile`` then reads the actual instances and never
allocates. An image or platform the adapter does not support is refused with
``PLATFORM_UNSUPPORTED`` before any call.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "AllocationReceipt",
    "AllocationRequest",
    "AllocationStore",
    "InstanceAdapter",
    "MemoryAllocationStore",
    "ProvisionError",
    "Provisioner",
]


class ProvisionError(RuntimeError):
    """A provisioning refusal; ``code`` is closed."""

    def __init__(self, code: str, allocation_id: str = "") -> None:
        self.code = code
        self.allocation_id = allocation_id
        super().__init__(code)


@dataclass(frozen=True)
class AllocationRequest:
    idempotency_key: str
    image: str
    platform: str
    count: int = 1

    def digest(self) -> str:
        body = json.dumps(
            [self.idempotency_key, self.image, self.platform, self.count],
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class AllocationReceipt:
    allocation_id: str
    instance_ids: tuple[str, ...]
    digest: str


class InstanceAdapter(Protocol):
    """The platform side. ``allocate`` may raise ``TimeoutError`` for an unknown outcome."""

    def supports(self, image: str, platform: str) -> bool: ...

    def allocate(
        self, allocation_id: str, request: AllocationRequest
    ) -> tuple[str, ...]: ...

    def instances(self, allocation_id: str) -> tuple[str, ...]: ...


class AllocationStore(Protocol):
    """Durable record of requests and receipts, keyed by idempotency key."""

    def request_for(self, key: str) -> AllocationRequest | None: ...

    def receipt_for(self, key: str) -> AllocationReceipt | None: ...

    def put_request(self, request: AllocationRequest, allocation_id: str) -> None: ...

    def put_receipt(self, key: str, receipt: AllocationReceipt) -> None: ...

    def key_of(self, allocation_id: str) -> str | None: ...


class MemoryAllocationStore:
    """In-memory store for the conformance kit and tests."""

    def __init__(self) -> None:
        self._requests: dict[str, AllocationRequest] = {}
        self._receipts: dict[str, AllocationReceipt] = {}
        self._keys: dict[str, str] = {}

    def request_for(self, key: str) -> AllocationRequest | None:
        return self._requests.get(key)

    def receipt_for(self, key: str) -> AllocationReceipt | None:
        return self._receipts.get(key)

    def put_request(self, request: AllocationRequest, allocation_id: str) -> None:
        self._requests[request.idempotency_key] = request
        self._keys[allocation_id] = request.idempotency_key

    def put_receipt(self, key: str, receipt: AllocationReceipt) -> None:
        self._receipts[key] = receipt

    def key_of(self, allocation_id: str) -> str | None:
        return self._keys.get(allocation_id)


def _receipt(allocation_id: str, instances: tuple[str, ...]) -> AllocationReceipt:
    body = json.dumps([allocation_id, sorted(instances)], separators=(",", ":"))
    return AllocationReceipt(
        allocation_id, instances, hashlib.sha256(body.encode()).hexdigest()
    )


class Provisioner:
    """Allocate through an adapter, fenced by the idempotency key."""

    def __init__(self, adapter: InstanceAdapter, store: AllocationStore) -> None:
        self._adapter = adapter
        self._store = store

    def allocate(self, request: AllocationRequest) -> AllocationReceipt:
        key = request.idempotency_key
        prior = self._store.request_for(key)
        if prior is not None:
            if prior.digest() != request.digest():
                raise ProvisionError("IDEMPOTENCY_CONFLICT")
            replay = self._store.receipt_for(key)
            if replay is not None:
                return replay
            raise ProvisionError("ALLOCATION_UNKNOWN", _allocation_id(key))
        if not self._adapter.supports(request.image, request.platform):
            raise ProvisionError("PLATFORM_UNSUPPORTED")
        allocation_id = _allocation_id(key)
        self._store.put_request(request, allocation_id)
        try:
            instances = self._adapter.allocate(allocation_id, request)
        except (TimeoutError, ConnectionError):
            raise ProvisionError("ALLOCATION_UNKNOWN", allocation_id) from None
        receipt = _receipt(allocation_id, instances)
        self._store.put_receipt(key, receipt)
        return receipt

    def reconcile(self, allocation_id: str) -> AllocationReceipt:
        """Read the actual instances of a recorded allocation; never allocates."""
        key = self._store.key_of(allocation_id)
        if key is None:
            raise ProvisionError("ALLOCATION_NOT_FOUND", allocation_id)
        known = self._store.receipt_for(key)
        if known is not None:
            return known
        instances = self._adapter.instances(allocation_id)
        if not instances:
            raise ProvisionError("ALLOCATION_UNKNOWN", allocation_id)
        receipt = _receipt(allocation_id, instances)
        self._store.put_receipt(key, receipt)
        return receipt


def _allocation_id(key: str) -> str:
    return "alloc-" + hashlib.sha256(key.encode()).hexdigest()[:24]
