"""The sandbox allocation port, its refusals and its receipt (R051.1, .2, .4, .5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sandbox_support import DisposableBackend

from scion_sdk.adapters.sandbox_provisioner import GuardedSandboxProvisioner
from scion_sdk.ports import (
    ProvisionRequest,
    ProvisionResult,
    SandboxProvisioner,
    SandboxState,
)
from scion_sdk.ports.sandbox import (
    REFUSAL_CODES,
    SandboxBackend,
    SandboxMatrix,
    SandboxReceipt,
    SandboxRefused,
    digest_of,
)
from scion_sdk.testing.sandbox_conformance import (
    conformance_request,
    run_sandbox_conformance,
)

MATRIX = SandboxMatrix(revision="m1", profiles=frozenset({"small"}))


def _provider(backend: DisposableBackend) -> GuardedSandboxProvisioner:
    return GuardedSandboxProvisioner(backend, MATRIX, adapter_version="1.0.0")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051.1")
def test_models_round_trip_and_reject_blank_identity() -> None:
    request = conformance_request("small")
    assert ProvisionRequest.model_validate(request.model_dump()) == request
    with pytest.raises(ValidationError):
        request.model_copy(update={}).__class__(
            **{**request.model_dump(), "operation_id": " "}
        )
    with pytest.raises(ValidationError):
        ProvisionRequest(**{**request.model_dump(), "instance_count": 0})
    assert {s.value for s in SandboxState} == {
        "allocating",
        "ready",
        "failed",
        "released",
    }
    stamp = datetime.now(UTC)
    result = ProvisionResult(
        allocation_id="a",
        instance_ids=("i",),
        image_digest="b" * 64,
        platform="linux/amd64",
        state=SandboxState.READY,
        allocated_at=stamp,
        updated_at=stamp,
        provider_receipt_ref="r",
    )
    assert ProvisionResult.model_validate(result.model_dump()) == result


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051.2")
async def test_replay_returns_reconciled_allocation() -> None:
    backend = DisposableBackend()
    provider = _provider(backend)
    await run_sandbox_conformance(provider, "small")
    assert backend.allocations == 1
    lost = DisposableBackend()
    lost.lose_response = True
    request = conformance_request("small", operation_id="op-lost")
    result = await _provider(lost).provision(request)
    assert lost.allocations == 1 and result.allocation_id == "alloc-1"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051.4")
async def test_refusals_make_zero_provider_calls() -> None:
    backend = DisposableBackend()
    provider = _provider(backend)
    good = conformance_request("small")
    cases = {
        "unsupported_profile": good.model_copy(update={"profile": "huge"}),
        "missing_grant": good.model_copy(update={"grant_ref": None}),
        "request_expired": good.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        ),
    }
    for code, request in cases.items():
        with pytest.raises(SandboxRefused) as caught:
            await provider.provision(request)
        assert caught.value.code == code
    assert backend.allocations == 0 and backend.records == {}
    await provider.provision(good)
    with pytest.raises(SandboxRefused) as caught:
        await provider.provision(good.model_copy(update={"instance_count": 2}))
    assert caught.value.code == "conflicting_replay" and backend.allocations == 1
    undecided = DisposableBackend()
    undecided.lose_response = True
    undecided.find_fails = False
    broken = _provider(undecided)
    undecided.find_fails = True
    with pytest.raises(SandboxRefused) as caught:
        await broken.provision(good)
    assert caught.value.code == "unresolved_outcome"
    assert set(REFUSAL_CODES) == {
        "unsupported_profile", "missing_grant", "request_expired",
        "conflicting_replay", "unresolved_outcome",
    }  # fmt: skip


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R051.5")
async def test_receipt_binds_digests_without_secrets() -> None:
    backend = DisposableBackend()
    provider = _provider(backend)
    request = conformance_request("small")
    result = await provider.provision(request)
    receipt = provider.receipt(request, result)
    assert isinstance(receipt, SandboxReceipt)
    assert isinstance(provider, SandboxProvisioner) and isinstance(
        backend, SandboxBackend
    )
    assert receipt.request_digest == digest_of(request)
    assert receipt.matrix_revision == "m1" and receipt.adapter_version == "1.0.0"
    assert receipt.allocation_id == result.allocation_id
    read_back = await provider.reconcile(request.operation_id)
    assert read_back == result
    text = receipt.model_dump_json()
    assert request.grant_ref not in text and "secret" not in text.lower()
