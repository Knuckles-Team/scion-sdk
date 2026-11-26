"""The SDK provisioner against a disposable adapter (R906)."""

from __future__ import annotations

import pytest

from scion_sdk.provisioning import (
    AllocationReceipt,
    AllocationRequest,
    AllocationStore,
    InstanceAdapter,
    MemoryAllocationStore,
    Provisioner,
    ProvisionError,
)


class _Adapter:
    def __init__(self) -> None:
        self.allocations: dict[str, tuple[str, ...]] = {}
        self.calls = 0
        self.lose_reply = False

    def supports(self, image: str, platform: str) -> bool:
        return platform == "linux/amd64"

    def allocate(
        self, allocation_id: str, request: AllocationRequest
    ) -> tuple[str, ...]:
        self.calls += 1
        ids = tuple(f"{allocation_id}-{n}" for n in range(request.count))
        self.allocations[allocation_id] = ids
        if self.lose_reply:
            raise TimeoutError
        return ids

    def instances(self, allocation_id: str) -> tuple[str, ...]:
        return self.allocations.get(allocation_id, ())


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R906")
def test_allocate_replay_unknown_reconcile() -> None:
    adapter = _Adapter()
    contract: InstanceAdapter = adapter
    store: AllocationStore = MemoryAllocationStore()
    provisioner = Provisioner(contract, store)
    request = AllocationRequest("k1", "img", "linux/amd64", 2)
    receipt = provisioner.allocate(request)
    assert isinstance(receipt, AllocationReceipt)
    assert len(receipt.instance_ids) == 2 and len(receipt.digest) == 64
    assert provisioner.allocate(request) == receipt and adapter.calls == 1
    with pytest.raises(ProvisionError) as conflict:
        provisioner.allocate(AllocationRequest("k1", "img", "linux/amd64", 3))
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ProvisionError) as unsupported:
        provisioner.allocate(AllocationRequest("k2", "img", "plan9/arm", 1))
    assert unsupported.value.code == "PLATFORM_UNSUPPORTED" and adapter.calls == 1
    adapter.lose_reply = True
    with pytest.raises(ProvisionError) as unknown:
        provisioner.allocate(AllocationRequest("k3", "img", "linux/amd64", 1))
    assert unknown.value.code == "ALLOCATION_UNKNOWN"
    calls = adapter.calls
    reconciled = provisioner.reconcile(unknown.value.allocation_id)
    assert reconciled.instance_ids == adapter.allocations[unknown.value.allocation_id]
    assert adapter.calls == calls
