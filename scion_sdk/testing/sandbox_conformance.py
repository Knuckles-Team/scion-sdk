"""Conformance suite for sandbox providers (SCION-CONNECTOR-CONTROL-R051.2).

``run_sandbox_conformance`` asserts the replay, conflict and read-back rules of
:class:`~scion_sdk.ports.sandbox.SandboxProvisioner` for any provider.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scion_sdk.ports.sandbox import (
    ProvisionRequest,
    SandboxProvisioner,
    SandboxRefused,
)

__all__ = ["conformance_request", "run_sandbox_conformance"]


def conformance_request(
    profile: str, *, operation_id: str = "op-conformance", count: int = 1
) -> ProvisionRequest:
    """A valid request for ``profile`` that expires in an hour."""
    return ProvisionRequest(
        operation_id=operation_id,
        tenant_id="tenant-conformance",
        profile=profile,
        image_digest="a" * 64,
        platform="linux/amd64",
        instance_count=count,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        grant_ref="grant://conformance",
    )


async def run_sandbox_conformance(provider: SandboxProvisioner, profile: str) -> None:
    """Raise ``AssertionError`` when ``provider`` breaks a port rule."""
    request = conformance_request(profile)
    first = await provider.provision(request)
    assert await provider.provision(request) == first, "replay must not reallocate"
    assert await provider.reconcile(request.operation_id) == first
    other = request.model_copy(update={"instance_count": request.instance_count + 1})
    try:
        await provider.provision(other)
    except SandboxRefused as refusal:
        assert refusal.code == "conflicting_replay"
    else:
        raise AssertionError("a different request under one operation id must refuse")
    assert await provider.reconcile("op-never-seen") is None
