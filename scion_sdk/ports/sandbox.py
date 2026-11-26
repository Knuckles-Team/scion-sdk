"""The sandbox allocation port (SCION-CONNECTOR-CONTROL-R051).

One typed port allocates sandbox instances for a run. The provider holds no
process-local record: the allocation backend is the authority, and a repeat of
``provision`` or a ``reconcile`` reads it. Transport loss never proves that no
allocation exists, so an uncertain outcome is reconciled before any retry.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

__all__ = [
    "REFUSAL_CODES",
    "ProvisionRequest",
    "ProvisionResult",
    "SandboxBackend",
    "SandboxMatrix",
    "SandboxProvisioner",
    "SandboxReceipt",
    "SandboxRefused",
    "SandboxState",
    "StoredAllocation",
    "digest_of",
]

REFUSAL_CODES = (
    "unsupported_profile",
    "missing_grant",
    "request_expired",
    "conflicting_replay",
    "unresolved_outcome",
)
_ID = r"^\S(?:.*\S)?$"
_DIGEST = r"^[0-9a-f]{64}$"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SandboxState(StrEnum):
    ALLOCATING = "allocating"
    READY = "ready"
    FAILED = "failed"
    RELEASED = "released"


class ProvisionRequest(_Frozen):
    """What one run asks the provider to allocate."""

    operation_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    tenant_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    profile: str = Field(min_length=1, max_length=128, pattern=_ID)
    image_digest: str = Field(pattern=_DIGEST)
    platform: str = Field(min_length=1, max_length=64, pattern=_ID)
    instance_count: int = Field(ge=1, le=1024)
    expires_at: AwareDatetime
    #: Reference to the grant that authorizes the allocation; never its value.
    grant_ref: str | None = Field(default=None, min_length=1, max_length=512)


class ProvisionResult(_Frozen):
    """What the provider observed after allocating."""

    allocation_id: str = Field(min_length=1, max_length=256, pattern=_ID)
    instance_ids: tuple[str, ...] = Field(min_length=1)
    image_digest: str = Field(pattern=_DIGEST)
    platform: str = Field(min_length=1)
    state: SandboxState
    allocated_at: AwareDatetime
    updated_at: AwareDatetime
    provider_receipt_ref: str = Field(min_length=1, max_length=512)


class SandboxMatrix(_Frozen):
    """The certified support matrix a provider serves."""

    revision: str = Field(min_length=1)
    profiles: frozenset[str]


class StoredAllocation(_Frozen):
    """The backend's durable record for one operation id."""

    request_digest: str = Field(pattern=_DIGEST)
    result: ProvisionResult


class SandboxReceipt(_Frozen):
    """Evidence of one allocation; holds no credentials or secret values."""

    request_digest: str = Field(pattern=_DIGEST)
    result_digest: str = Field(pattern=_DIGEST)
    adapter_version: str
    matrix_revision: str
    operation_id: str
    allocation_id: str
    instance_ids: tuple[str, ...]
    state: SandboxState
    allocated_at: AwareDatetime
    updated_at: AwareDatetime
    reconcile_outcome: str


class SandboxRefused(RuntimeError):
    """The provider refused; ``code`` is one of :data:`REFUSAL_CODES`."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def digest_of(model: BaseModel) -> str:
    """SHA-256 of the model's canonical JSON."""
    body = json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@runtime_checkable
class SandboxBackend(Protocol):
    """The allocation authority a provider talks to."""

    async def find(self, operation_id: str) -> StoredAllocation | None:
        """Read the durable record of ``operation_id``; ``None`` when absent."""
        ...

    async def allocate(
        self, request: ProvisionRequest, request_digest: str
    ) -> ProvisionResult:
        """Allocate and record ``request_digest`` against the operation id."""
        ...

    async def release(self, allocation_id: str) -> ProvisionResult:
        """Release an allocation."""
        ...


@runtime_checkable
class SandboxProvisioner(Protocol):
    """Allocates once per ``operation_id`` and reads back what exists."""

    async def provision(self, request: ProvisionRequest) -> ProvisionResult: ...

    async def reconcile(self, operation_id: str) -> ProvisionResult | None: ...

    async def release(self, allocation_id: str) -> ProvisionResult: ...
