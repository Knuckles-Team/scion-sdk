"""A disposable in-memory sandbox backend for the port tests."""

from __future__ import annotations

from datetime import UTC, datetime

from scion_sdk.ports.sandbox import (
    ProvisionRequest,
    ProvisionResult,
    SandboxState,
    StoredAllocation,
)


class DisposableBackend:
    """Records allocations; can lose the response after allocating."""

    def __init__(self) -> None:
        self.records: dict[str, StoredAllocation] = {}
        self.allocations = 0
        self.lose_response = False
        self.find_fails = False

    async def find(self, operation_id: str) -> StoredAllocation | None:
        if self.find_fails:
            raise ConnectionError("backend unreachable")
        return self.records.get(operation_id)

    async def allocate(
        self, request: ProvisionRequest, request_digest: str
    ) -> ProvisionResult:
        self.allocations += 1
        stamp = datetime.now(UTC)
        result = ProvisionResult(
            allocation_id=f"alloc-{self.allocations}",
            instance_ids=tuple(
                f"{request.operation_id}-{i}" for i in range(request.instance_count)
            ),
            image_digest=request.image_digest,
            platform=request.platform,
            state=SandboxState.READY,
            allocated_at=stamp,
            updated_at=stamp,
            provider_receipt_ref=f"provider://receipt/{self.allocations}",
        )
        self.records[request.operation_id] = StoredAllocation(
            request_digest=request_digest, result=result
        )
        if self.lose_response:
            raise ConnectionError("response lost")
        return result

    async def release(self, allocation_id: str) -> ProvisionResult:
        for record in self.records.values():
            if record.result.allocation_id == allocation_id:
                return record.result.model_copy(update={"state": SandboxState.RELEASED})
        raise KeyError(allocation_id)
