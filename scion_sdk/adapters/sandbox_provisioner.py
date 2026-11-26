"""A guarded provisioner over one allocation backend (SCION-CONNECTOR-CONTROL-R051.2/.4/.5).

Refusals run before any backend call. Replay and reconciliation read the
backend; this class keeps no record of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from scion_sdk.ports.sandbox import (
    ProvisionRequest,
    ProvisionResult,
    SandboxBackend,
    SandboxMatrix,
    SandboxReceipt,
    SandboxRefused,
    digest_of,
)

__all__ = ["GuardedSandboxProvisioner"]


class GuardedSandboxProvisioner:
    """Implements :class:`~scion_sdk.ports.sandbox.SandboxProvisioner`."""

    def __init__(
        self,
        backend: SandboxBackend,
        matrix: SandboxMatrix,
        *,
        adapter_version: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._backend = backend
        self._matrix = matrix
        self._adapter_version = adapter_version
        self._clock = clock
        self.last_reconcile_outcome = "none"

    def _refuse_before_call(self, request: ProvisionRequest) -> None:
        if not request.grant_ref:
            raise SandboxRefused("missing_grant")
        if request.profile not in self._matrix.profiles:
            raise SandboxRefused("unsupported_profile")
        if request.expires_at <= self._clock():
            raise SandboxRefused("request_expired")

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        """Allocate once per ``operation_id``; a replay reads the record."""
        self._refuse_before_call(request)
        digest = digest_of(request)
        try:
            existing = await self._backend.find(request.operation_id)
        except Exception:
            self.last_reconcile_outcome = "undecided"
            raise SandboxRefused("unresolved_outcome") from None
        if existing is not None:
            if existing.request_digest != digest:
                raise SandboxRefused("conflicting_replay")
            self.last_reconcile_outcome = "replayed"
            return existing.result
        try:
            return await self._backend.allocate(request, digest)
        except Exception:
            return await self._reconciled_or_refused(request.operation_id, digest)

    async def _reconciled_or_refused(
        self, operation_id: str, digest: str
    ) -> ProvisionResult:
        # Transport loss never proves that no allocation exists: read it back.
        try:
            found = await self._backend.find(operation_id)
        except Exception:
            self.last_reconcile_outcome = "undecided"
            raise SandboxRefused("unresolved_outcome") from None
        if found is None:
            self.last_reconcile_outcome = "absent"
            raise SandboxRefused("unresolved_outcome")
        if found.request_digest != digest:
            raise SandboxRefused("conflicting_replay")
        self.last_reconcile_outcome = "found"
        return found.result

    async def reconcile(self, operation_id: str) -> ProvisionResult | None:
        """The recorded allocation of ``operation_id``, or ``None``."""
        try:
            found = await self._backend.find(operation_id)
        except Exception:
            self.last_reconcile_outcome = "undecided"
            raise SandboxRefused("unresolved_outcome") from None
        self.last_reconcile_outcome = "found" if found else "absent"
        return None if found is None else found.result

    async def release(self, allocation_id: str) -> ProvisionResult:
        return await self._backend.release(allocation_id)

    def receipt(
        self, request: ProvisionRequest, result: ProvisionResult
    ) -> SandboxReceipt:
        """Evidence binding the request and result digests; no credentials."""
        return SandboxReceipt(
            request_digest=digest_of(request),
            result_digest=digest_of(result),
            adapter_version=self._adapter_version,
            matrix_revision=self._matrix.revision,
            operation_id=request.operation_id,
            allocation_id=result.allocation_id,
            instance_ids=result.instance_ids,
            state=result.state,
            allocated_at=result.allocated_at,
            updated_at=result.updated_at,
            reconcile_outcome=self.last_reconcile_outcome,
        )
