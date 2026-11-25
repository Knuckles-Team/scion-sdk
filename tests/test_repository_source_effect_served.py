"""The repository effect served through the provider's business API.

SCION-GOVERNED-WRITEBACK-R004.4, R004.6, R004.7 and R004.8: the adapter
profile must be complete and bound by the intent's contract digest, the
provider's business-rule rejection is final for the request, the write
path resolves against the trusted scope and the immutable base tree at
preview and effect time with no normalization, the audit-bound effect
matches the exact preview, the recorded approval, the contract pin, the
source version and the audit reservation before the provider is called,
and the commit itself is conditional — no force option, no unconditional
fallback.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

import pytest
from medulla.generated.write_back import SourceChangeSet, WriteBackEffectStatus

from scion_sdk.ports.audit_reservation import AuditReservation
from scion_sdk.ports.repository_provider import (
    BaseStale,
    ConditionalCommit,
    FileStateMismatch,
    RepositoryBusinessRuleRejected,
    RepositoryCommitReceipt,
    RepositoryProviderPort,
    RepositoryTreeKind,
)
from scion_sdk.ports.writeback_ledger import WriteBackLedger
from scion_sdk.testing.writeback import make_writeback_fixture
from scion_sdk.writeback.repository_effect import (
    RepositorySourceEffectIntent,
    RepositorySourceEffectRequest,
)
from scion_sdk.writeback.repository_transport import (
    REPOSITORY_WRITE_SCOPES,
    AdapterProfileIncompleteError,
    ApprovalBindingMismatchError,
    BaseStaleError,
    BusinessRuleRejectedError,
    FileStateMismatchError,
    PathEscapesError,
    PathOutOfScopeError,
    PathUnsupportedKindError,
    ProviderLacksConditionalUpdateError,
    RepositoryAdapterProfile,
    RepositoryEffectRefused,
    RepositoryEffectTransport,
    RepositoryScope,
    ScopeUnconfiguredError,
    resolve_scope,
    scopes_from_trusted_config,
)

_REPOSITORY_ID = "repository-1"
_TARGET_REF = "refs/heads/main"
_BASE_REVISION = "a" * 40

BASE_TREE: dict[str, RepositoryTreeKind] = {
    "nested": "tree",
    "nested/file.bin": "file",
    "nested/link": "symlink",
    "nested/sub": "submodule",
}

SCOPE_CONFIG = {
    REPOSITORY_WRITE_SCOPES: (
        "[{"
        f'"repository_id": "{_REPOSITORY_ID}", '
        f'"target_refs": ["{_TARGET_REF}"], '
        '"path_prefixes": ["nested"]'
        "}]"
    )
}


class _FakeProvider:
    """A business-API provider whose every call is recorded."""

    def __init__(
        self,
        *,
        reject: bool = False,
        base_stale: bool = False,
        file_mismatch: bool = False,
        conditional: bool = True,
        operation: str = "repository.commit_file",
    ) -> None:
        self.calls: list[ConditionalCommit] = []
        self.writes: list[ConditionalCommit] = []
        self.storage_calls: list[str] = []
        self.last_receipt: RepositoryCommitReceipt | None = None
        self.supports_conditional_update = conditional
        self._reject = reject
        self._base_stale = base_stale
        self._file_mismatch = file_mismatch
        self._operation = operation

    async def commit_file(
        self, operation: str, commit: ConditionalCommit
    ) -> RepositoryCommitReceipt:
        del operation
        self.calls.append(commit)
        if self._base_stale:
            raise BaseStale("target ref moved off the base revision")
        if self._file_mismatch:
            raise FileStateMismatch("file state no longer matches the expected digest")
        if self._reject:
            raise RepositoryBusinessRuleRejected("vendor rule R-7 blocks this commit")
        self.writes.append(commit)
        self.last_receipt = RepositoryCommitReceipt(
            business_api_operation=self._operation, effect_revision="e" * 40
        )
        return self.last_receipt

    async def store_file(self, relative_path: str, payload: bytes) -> None:
        self.storage_calls.append(relative_path)
        del payload


def _intent_dict(
    profile: RepositoryAdapterProfile, *, path: str, action: str
) -> dict[str, Any]:
    payload = b"\x00hello\n" if action == "update" else b"new file\n"
    data: dict[str, Any] = {
        "schema_version": 1,
        "repository_id": _REPOSITORY_ID,
        "target_ref": _TARGET_REF,
        "relative_path": path,
        "base_revision": _BASE_REVISION,
        "action": action,
        "payload_base64": base64.b64encode(payload).decode(),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_size": len(payload),
        "executable": False,
        "adapter_contract_digest": profile.contract_digest(),
    }
    if action == "update":
        data["expected_file_digest"] = "b" * 64
        data["expected_executable"] = False
    else:
        data["expected_file_digest"] = None
        data["expected_executable"] = None
    return data


def _request(
    profile: RepositoryAdapterProfile, *, path: str, action: str
) -> RepositorySourceEffectRequest:
    intent = RepositorySourceEffectIntent.model_validate(
        _intent_dict(profile, path=path, action=action)
    )
    change = make_writeback_fixture().change_set.model_copy(
        update={
            "entity_id": _REPOSITORY_ID,
            "base_source_version": _BASE_REVISION,
            "desired_patch": {"repository_effect": intent.model_dump(mode="json")},
            "field_scope": ["repository_effect"],
            "field_provenance": {"repository_effect": "fixture"},
        }
    )
    change = change.model_copy(update={"change_set_digest": change.canonical_digest()})
    return RepositorySourceEffectRequest(
        schema_version=1, change_set=change, intent=intent
    )


class _Ledger:
    """The durable change-set store the recorded approval lives in."""

    def __init__(self) -> None:
        self.change_sets: dict[tuple[str, str], SourceChangeSet] = {}

    async def create(self, change_set: SourceChangeSet) -> SourceChangeSet:
        key = (change_set.tenant_id, change_set.change_set_id)
        existing = self.change_sets.get(key)
        if existing is not None and existing != change_set:
            raise ValueError("change-set identity conflict")
        self.change_sets[key] = change_set
        return change_set

    async def get(self, tenant_id: str, change_set_id: str) -> SourceChangeSet | None:
        return self.change_sets.get((tenant_id, change_set_id))


def _reservation(change_set: SourceChangeSet) -> AuditReservation:
    return AuditReservation(
        reservation_id="reservation:1",
        effect_key=change_set.idempotency_key,
        change_set_digest=change_set.change_set_digest,
    )


def _transport(
    provider: _FakeProvider,
    profile: RepositoryAdapterProfile,
    *,
    ledger: WriteBackLedger | None = None,
    base_revision: str = _BASE_REVISION,
) -> RepositoryEffectTransport:
    return RepositoryEffectTransport(
        provider,
        profile,
        scopes_from_trusted_config(SCOPE_CONFIG),
        repository_id=_REPOSITORY_ID,
        base_revision=base_revision,
        base_tree=BASE_TREE,
        ledger=ledger or _Ledger(),
    )


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R004.4")
async def test_business_api() -> None:
    profile = RepositoryAdapterProfile(
        business_api_operation="repository.commit_file", max_payload_size=4096
    )
    request = _request(profile, path="nested/file.bin", action="update")
    change = request.change_set
    provider = _FakeProvider()
    assert isinstance(provider, RepositoryProviderPort)
    ledger = _Ledger()
    await ledger.create(change)
    transport = _transport(provider, profile, ledger=ledger)
    reservation = _reservation(change)

    # An accepted mutation: one business-API call, receipt names the operation.
    attempt = await transport.apply_reserved(
        change, change.base_source_version, reservation
    )
    assert attempt.effect_status is WriteBackEffectStatus.APPLIED
    assert attempt.change_set_digest == change.change_set_digest
    assert attempt.post_source_version == "e" * 40
    assert len(provider.calls) == 1
    commit = provider.calls[0]
    assert commit.relative_path == "nested/file.bin"
    assert commit.payload == b"\x00hello\n"
    assert commit.idempotency_key == change.idempotency_key
    assert provider.last_receipt is not None
    assert provider.last_receipt.business_api_operation == "repository.commit_file"
    assert provider.storage_calls == []

    # A replay of the idempotency key returns the recorded effect once.
    assert await transport.prior_effect(change) == attempt
    assert len(provider.calls) == 1

    # A business-rule rejection is final: the request never reaches any
    # other provider method, and no effect is recorded.
    provider = _FakeProvider(reject=True)
    ledger = _Ledger()
    await ledger.create(change)
    transport = _transport(provider, profile, ledger=ledger)
    with pytest.raises(BusinessRuleRejectedError) as raised:
        await transport.apply_reserved(
            change, change.base_source_version, _reservation(change)
        )
    refusal = raised.value
    assert isinstance(refusal, RepositoryEffectRefused)
    assert refusal.code == "BUSINESS_RULE_REJECTED"
    assert refusal.detail == "vendor rule R-7 blocks this commit"
    assert len(provider.calls) == 1
    assert provider.storage_calls == []
    assert await transport.prior_effect(change) is None

    # A blank operation refuses before any provider call.
    provider = _FakeProvider()
    ledger = _Ledger()
    await ledger.create(change)
    transport = _transport(
        provider,
        RepositoryAdapterProfile(business_api_operation="  ", max_payload_size=4096),
        ledger=ledger,
    )
    with pytest.raises(AdapterProfileIncompleteError) as raised:
        await transport.apply_reserved(
            change, change.base_source_version, _reservation(change)
        )
    assert raised.value.code == "ADAPTER_PROFILE_INCOMPLETE"
    assert provider.calls == []

    # A profile the intent does not pin refuses before any provider call.
    provider = _FakeProvider()
    ledger = _Ledger()
    await ledger.create(change)
    transport = _transport(
        provider,
        RepositoryAdapterProfile(
            business_api_operation="repository.commit_file", max_payload_size=1024
        ),
        ledger=ledger,
    )
    with pytest.raises(AdapterProfileIncompleteError):
        await transport.apply_reserved(
            change, change.base_source_version, _reservation(change)
        )
    assert provider.calls == []

    # The provider cannot acknowledge a different operation.
    provider = _FakeProvider(operation="repository.other")
    ledger = _Ledger()
    await ledger.create(change)
    transport = _transport(provider, profile, ledger=ledger)
    with pytest.raises(AdapterProfileIncompleteError):
        await transport.apply_reserved(
            change, change.base_source_version, _reservation(change)
        )
    assert await transport.prior_effect(change) is None


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R004.6")
async def test_path_scope() -> None:
    # The trusted configuration is the only scope source.
    scopes = scopes_from_trusted_config(SCOPE_CONFIG)
    assert scopes == (
        RepositoryScope(
            repository_id=_REPOSITORY_ID,
            target_refs=(_TARGET_REF,),
            path_prefixes=("nested",),
        ),
    )
    assert scopes == scopes_from_trusted_config(
        {
            REPOSITORY_WRITE_SCOPES: [
                {
                    "repository_id": _REPOSITORY_ID,
                    "target_refs": [_TARGET_REF],
                    "path_prefixes": ["nested"],
                }
            ]
        }
    )
    assert scopes_from_trusted_config({}) == ()

    for bad in (
        {"REPOSITORY_WRITE_SCOPES": "not json"},
        {"REPOSITORY_WRITE_SCOPES": "{'repository_id': 'r'}"},
        {REPOSITORY_WRITE_SCOPES: [{"repository_id": "r", "target_refs": ["t"]}]},
        {
            REPOSITORY_WRITE_SCOPES: [
                {
                    "repository_id": "r",
                    "target_refs": ["t"],
                    "path_prefixes": ["../x"],
                }
            ]
        },
    ):
        with pytest.raises(ScopeUnconfiguredError):
            scopes_from_trusted_config(bad)

    def resolve(relative_path: str, **overrides: Any) -> RepositoryScope:
        return resolve_scope(
            scopes,
            repository_id=overrides.get("repository_id", _REPOSITORY_ID),
            target_ref=overrides.get("target_ref", _TARGET_REF),
            relative_path=relative_path,
            tree=BASE_TREE,
        )

    # Allowed: an existing regular file and a new file under the prefix.
    assert resolve("nested/file.bin").path_prefixes == ("nested",)
    assert resolve("nested/new.bin").path_prefixes == ("nested",)

    # Every escape and scope variant refuses.
    for path, error in (
        ("top/file.bin", PathOutOfScopeError),
        ("/nested/file.bin", PathEscapesError),
        ("nested/../file.bin", PathEscapesError),
        ("nested/%2e%2e/file.bin", PathEscapesError),
        (r"nested/a\b.bin", PathEscapesError),
        ("C:/nested/file.bin", PathEscapesError),
        ("nested/link/inner.bin", PathUnsupportedKindError),
        ("nested/sub/file.bin", PathUnsupportedKindError),
        ("nested", PathUnsupportedKindError),
        ("nested/missing/child.bin", PathUnsupportedKindError),
    ):
        with pytest.raises(error):
            resolve(path)

    # A target or repository the scope does not name configures no scope.
    for overrides in (
        {"target_ref": "refs/heads/dev"},
        {"repository_id": "repository-2"},
    ):
        with pytest.raises(ScopeUnconfiguredError):
            resolve("nested/file.bin", **overrides)
    with pytest.raises(ScopeUnconfiguredError):
        resolve_scope(
            (),
            repository_id=_REPOSITORY_ID,
            target_ref=_TARGET_REF,
            relative_path="nested/file.bin",
            tree=BASE_TREE,
        )

    # Ambiguous: two scopes claiming the same repository and target.
    duplicate = RepositoryScope(
        repository_id=_REPOSITORY_ID,
        target_refs=(_TARGET_REF,),
        path_prefixes=("nested",),
    )
    with pytest.raises(ScopeUnconfiguredError):
        resolve_scope(
            (scopes[0], duplicate),
            repository_id=_REPOSITORY_ID,
            target_ref=_TARGET_REF,
            relative_path="nested/file.bin",
            tree=BASE_TREE,
        )

    # Only the allowed path changes: preview and effect resolve the scope,
    # and the refused path reaches no provider method.
    profile = RepositoryAdapterProfile(
        business_api_operation="repository.commit_file", max_payload_size=4096
    )
    allowed = _request(profile, path="nested/file.bin", action="update")
    provider = _FakeProvider()
    ledger = _Ledger()
    await ledger.create(allowed.change_set)
    transport = _transport(provider, profile, ledger=ledger)
    await transport.apply_reserved(
        allowed.change_set,
        allowed.change_set.base_source_version,
        _reservation(allowed.change_set),
    )
    assert [commit.relative_path for commit in provider.calls] == ["nested/file.bin"]

    refused = _request(profile, path="top/file.bin", action="update")
    provider = _FakeProvider()
    ledger = _Ledger()
    await ledger.create(refused.change_set)
    transport = _transport(provider, profile, ledger=ledger)
    current = await transport.read_current(refused.change_set)
    with pytest.raises(PathOutOfScopeError):
        await transport.preview(refused.change_set, current)
    with pytest.raises(PathOutOfScopeError):
        await transport.apply_reserved(
            refused.change_set,
            refused.change_set.base_source_version,
            _reservation(refused.change_set),
        )
    assert provider.calls == []
    assert provider.storage_calls == []


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R004.7")
async def test_approved_effect() -> None:
    profile = RepositoryAdapterProfile(
        business_api_operation="repository.commit_file", max_payload_size=4096
    )
    request = _request(profile, path="nested/file.bin", action="update")
    change = request.change_set
    ledger = _Ledger()
    await ledger.create(change)
    provider = _FakeProvider()
    transport = _transport(provider, profile, ledger=ledger)

    # The repository effect is audit-bound: no reservation, no provider call.
    with pytest.raises(ApprovalBindingMismatchError) as raised:
        await transport.apply(change, change.base_source_version)
    assert raised.value.code == "APPROVAL_BINDING_MISMATCH"
    assert provider.calls == []

    # The unchanged approved effect reaches the provider exactly once.
    attempt = await transport.apply_reserved(
        change, change.base_source_version, _reservation(change)
    )
    assert attempt.effect_status is WriteBackEffectStatus.APPLIED
    assert len(provider.calls) == 1

    # A substituted actor, connector, target, path, base, bytes, digest,
    # policy or approval refuses with zero provider calls.
    intent = dict(change.desired_patch["repository_effect"])
    substitutions = [
        ("actor", "principal:other"),
        ("connector_id", "other-connector"),
        ("tenant_id", "tenant-2"),
        ("policy_digest", "f" * 64),
        ("base_source_version", "b" * 40),
        (
            "authorization",
            change.authorization.model_copy(update={"decision_digest": "d" * 64}),
        ),
        (
            "desired_patch",
            {
                "repository_effect": {
                    **intent,
                    "relative_path": "nested/other.bin",
                }
            },
        ),
        (
            "desired_patch",
            {"repository_effect": {**intent, "target_ref": "refs/heads/dev"}},
        ),
        (
            "desired_patch",
            {
                "repository_effect": {
                    **intent,
                    "payload_base64": base64.b64encode(b"other bytes\n").decode(),
                    "payload_sha256": hashlib.sha256(b"other bytes\n").hexdigest(),
                    "payload_size": len(b"other bytes\n"),
                }
            },
        ),
        ("change_set_digest", "9" * 64),
    ]
    for field, value in substitutions:
        altered = change.model_copy(update={field: value})
        with pytest.raises(ApprovalBindingMismatchError) as raised:
            await transport.apply_reserved(
                altered, altered.base_source_version, _reservation(altered)
            )
        assert raised.value.code == "APPROVAL_BINDING_MISMATCH"
        assert len(provider.calls) == 1

    # A reservation that is not bound to the approved effect refuses.
    for reservation in (
        AuditReservation(
            reservation_id="reservation:2",
            effect_key=change.idempotency_key,
            change_set_digest="0" * 64,
        ),
        AuditReservation(
            reservation_id="  ",
            effect_key=change.idempotency_key,
            change_set_digest=change.change_set_digest,
        ),
    ):
        with pytest.raises(ApprovalBindingMismatchError):
            await transport.apply_reserved(
                change, change.base_source_version, reservation
            )
        assert len(provider.calls) == 1


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R004.8")
async def test_conditional_effect() -> None:
    profile = RepositoryAdapterProfile(
        business_api_operation="repository.commit_file", max_payload_size=4096
    )
    request = _request(profile, path="nested/file.bin", action="update")
    change = request.change_set
    reservation = _reservation(change)

    # A competing commit lands after preflight: the approved write refuses
    # with BASE_STALE and does not overwrite it.
    ledger = _Ledger()
    await ledger.create(change)
    provider = _FakeProvider(base_stale=True)
    transport = _transport(provider, profile, ledger=ledger)
    with pytest.raises(BaseStaleError) as raised:
        await transport.apply_reserved(change, change.base_source_version, reservation)
    assert raised.value.code == "BASE_STALE"
    assert provider.writes == []
    assert await transport.prior_effect(change) is None

    # The file state no longer matches the expected digest or mode.
    ledger = _Ledger()
    await ledger.create(change)
    provider = _FakeProvider(file_mismatch=True)
    transport = _transport(provider, profile, ledger=ledger)
    with pytest.raises(FileStateMismatchError) as raised:
        await transport.apply_reserved(change, change.base_source_version, reservation)
    assert raised.value.code == "FILE_STATE_MISMATCH"
    assert provider.writes == []
    assert await transport.prior_effect(change) is None

    # A provider that cannot offer the condition is not admitted: no call.
    ledger = _Ledger()
    await ledger.create(change)
    provider = _FakeProvider(conditional=False)
    transport = _transport(provider, profile, ledger=ledger)
    with pytest.raises(ProviderLacksConditionalUpdateError) as raised:
        await transport.apply_reserved(change, change.base_source_version, reservation)
    assert raised.value.code == "PROVIDER_LACKS_CONDITIONAL_UPDATE"
    assert provider.calls == []

    # An unchanged base produces one revision through one provider commit.
    ledger = _Ledger()
    await ledger.create(change)
    provider = _FakeProvider()
    transport = _transport(provider, profile, ledger=ledger)
    attempt = await transport.apply_reserved(
        change, change.base_source_version, reservation
    )
    assert attempt.effect_status is WriteBackEffectStatus.APPLIED
    assert attempt.pre_source_version == change.base_source_version
    assert attempt.post_source_version == "e" * 40
    assert len(provider.calls) == 1
    assert len(provider.writes) == 1
