"""The shared repository-effect transport over the provider's business API.

SCION-GOVERNED-WRITEBACK-R004.4 through R004.10. ``RepositoryEffectTransport``
implements ``WriteBackTransport`` over the ``RepositoryProviderPort``: the
pinned adapter profile must be complete and bound by the intent's
``adapter_contract_digest`` before any provider call, and the write path is
resolved against the trusted scope and the immutable base tree at preview
time and again at effect time. A provider business-rule rejection is final
for the request; no other path is tried. The effect is audit-bound and the
commit itself is conditional: a provider that cannot offer the atomic
condition is not admitted, and there is no force option or unconditional
fallback. Success needs the provider effect, the independent readback at
the actual effect revision and the durable attempt receipt joined by one
transaction: the receipt binds the canonical request, the approval, the
audit reservation and the provider effect revision, and a failed receipt
persistence establishes no success.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from medulla.generated.write_back import (
    ReconciliationObservation,
    SourceChangeSet,
    WriteBackAttempt,
    WriteBackEffectStatus,
)
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

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
from scion_sdk.ports.writeback_models import DryRunObservation, SourceSnapshot
from scion_sdk.writeback._contract_json import _decode_contract_json
from scion_sdk.writeback._transport_common import (
    applied_attempt,
    check_prior_effect,
    digest,
    preview_observation,
    reconciliation_observation,
)
from scion_sdk.writeback.dry_run_binding import (
    expected_preview,
    require_preview_binding,
)
from scion_sdk.writeback.errors import (
    ChangeSetValidationError,
    OutcomeUncertainError,
    SourceVersionConflictError,
    WriteBackError,
)
from scion_sdk.writeback.repository_effect import (
    RepositorySourceEffectIntent,
    RepositorySourceEffectReceipt,
    RepositorySourceEffectRequest,
    _check_repository_path,
)

__all__ = [
    "REPOSITORY_WRITE_SCOPES",
    "AdapterProfileIncompleteError",
    "ApprovalBindingMismatchError",
    "BaseStaleError",
    "BusinessRuleRejectedError",
    "FileStateMismatchError",
    "PathEscapesError",
    "PathOutOfScopeError",
    "PathUnsupportedKindError",
    "ProviderLacksConditionalUpdateError",
    "ReadbackMismatchError",
    "ReadbackUnavailableError",
    "ReceiptPersistenceFailedError",
    "ReceiptUnboundError",
    "RepositoryAdapterProfile",
    "RepositoryEffectRefused",
    "RepositoryEffectTransport",
    "RepositoryScope",
    "ScopeUnconfiguredError",
    "resolve_scope",
    "scopes_from_trusted_config",
]

#: The trusted connector configuration key that grants repository write scope.
REPOSITORY_WRITE_SCOPES = "REPOSITORY_WRITE_SCOPES"


class RepositoryEffectRefused(WriteBackError):
    """A governed repository-effect refusal carrying a stable code.

    Callers branch on :attr:`code`; :attr:`detail` is diagnostic text only.
    """

    code: ClassVar[str]

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


class AdapterProfileIncompleteError(RepositoryEffectRefused):
    """The adapter profile is blank or not bound by the intent's contract digest."""

    code = "ADAPTER_PROFILE_INCOMPLETE"


class ApprovalBindingMismatchError(RepositoryEffectRefused):
    """The pre-effect approval binding does not match the recorded approval."""

    code = "APPROVAL_BINDING_MISMATCH"


class BusinessRuleRejectedError(RepositoryEffectRefused):
    """The provider's business API rejected the effect for a business reason."""

    code = "BUSINESS_RULE_REJECTED"


class BaseStaleError(RepositoryEffectRefused):
    """The target ref moved off the approved base revision before the commit."""

    code = "BASE_STALE"


class FileStateMismatchError(RepositoryEffectRefused):
    """The file no longer matches the expected digest or mode before the commit."""

    code = "FILE_STATE_MISMATCH"


class ProviderLacksConditionalUpdateError(RepositoryEffectRefused):
    """The provider cannot offer the atomic commit condition and is not admitted."""

    code = "PROVIDER_LACKS_CONDITIONAL_UPDATE"


class ReadbackMismatchError(RepositoryEffectRefused, OutcomeUncertainError):
    """The independent readback does not match the approved bytes or mode.

    The commit may have landed; the outcome is uncertain, not successful.
    """

    code = "READBACK_MISMATCH"


class ReadbackUnavailableError(RepositoryEffectRefused, OutcomeUncertainError):
    """No independent read is available at the actual effect revision.

    The commit may have landed; the outcome is uncertain, not successful.
    """

    code = "READBACK_UNAVAILABLE"


class ReceiptUnboundError(RepositoryEffectRefused):
    """The effect receipt is not bound to the approved canonical request."""

    code = "RECEIPT_UNBOUND"


class ReceiptPersistenceFailedError(RepositoryEffectRefused):
    """The durable attempt receipt could not be persisted."""

    code = "RECEIPT_PERSISTENCE_FAILED"


class PathEscapesError(RepositoryEffectRefused):
    """The path spells an absolute location, a traversal or an alias."""

    code = "PATH_ESCAPES"


class PathOutOfScopeError(RepositoryEffectRefused):
    """The path is not under any prefix of the trusted write scope."""

    code = "PATH_OUT_OF_SCOPE"


class PathUnsupportedKindError(RepositoryEffectRefused):
    """The target is not a regular file or a new file under an allowed prefix."""

    code = "PATH_UNSUPPORTED_KIND"


class ScopeUnconfiguredError(RepositoryEffectRefused):
    """The trusted configuration yields no usable write scope."""

    code = "SCOPE_UNCONFIGURED"


class RepositoryAdapterProfile(BaseModel):
    """One pinned adapter capability, bound by the intent's contract digest.

    ``business_api_operation`` is the provider operation name the transport
    invokes; ``max_payload_size`` is the trusted size limit the admitted
    adapter may serve. The transport refuses a profile that is blank or
    whose :meth:`contract_digest` the intent does not pin.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    business_api_operation: str
    max_payload_size: StrictInt = Field(ge=0)

    def contract_digest(self) -> str:
        """The canonical digest the intent pins in ``adapter_contract_digest``."""
        return digest(self.model_dump(mode="json"))


class RepositoryScope(BaseModel):
    """One trusted write scope entry for a repository and target ref."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    repository_id: str = Field(min_length=1)
    target_refs: tuple[str, ...] = Field(min_length=1)
    path_prefixes: tuple[str, ...] = Field(min_length=1)


def _decode_scope_entries(raw: object) -> list[object]:
    """The JSON list behind the trusted configuration key, decoded strictly."""
    if isinstance(raw, str):
        try:
            raw = _decode_contract_json(raw.encode("utf-8"))
        except ValueError as exc:
            raise ScopeUnconfiguredError(
                "write scopes are not valid contract JSON"
            ) from exc
    if not isinstance(raw, list):
        raise ScopeUnconfiguredError("write scopes must be a JSON list of entries")
    return raw


def _check_prefix_shape(prefix: str) -> None:
    try:
        _check_repository_path(prefix)
    except ValueError as exc:
        raise ScopeUnconfiguredError(
            f"write scope prefix {prefix!r} is not a clean relative path"
        ) from exc


def scopes_from_trusted_config(
    config: Mapping[str, Any],
) -> tuple[RepositoryScope, ...]:
    """Parse the trusted connector configuration's repository write scopes.

    Only :data:`REPOSITORY_WRITE_SCOPES` grants write scope: a JSON list of
    ``{repository_id, target_refs, path_prefixes}`` entries. A missing key
    configures no scope; a malformed value is refused.
    """
    raw = config.get(REPOSITORY_WRITE_SCOPES)
    if raw is None:
        return ()
    scopes: list[RepositoryScope] = []
    for entry in _decode_scope_entries(raw):
        if not isinstance(entry, dict) or set(entry) != {
            "repository_id",
            "target_refs",
            "path_prefixes",
        }:
            raise ScopeUnconfiguredError("write scope entry has unknown fields")
        repository_id = entry["repository_id"]
        if not isinstance(repository_id, str) or not repository_id:
            raise ScopeUnconfiguredError("write scope needs a repository_id")
        for key in ("target_refs", "path_prefixes"):
            value = entry[key]
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item for item in value)
            ):
                raise ScopeUnconfiguredError(
                    f"write scope {key} must be a list of non-empty strings"
                )
        for prefix in entry["path_prefixes"]:
            _check_prefix_shape(prefix)
        scopes.append(
            RepositoryScope(
                repository_id=repository_id,
                target_refs=tuple(entry["target_refs"]),
                path_prefixes=tuple(entry["path_prefixes"]),
            )
        )
    return tuple(scopes)


def _match_scope(
    scopes: Sequence[RepositoryScope], *, repository_id: str, target_ref: str
) -> RepositoryScope:
    matches = [
        scope
        for scope in scopes
        if scope.repository_id == repository_id and target_ref in scope.target_refs
    ]
    if len(matches) != 1:
        raise ScopeUnconfiguredError(
            f"no unique write scope for repository {repository_id!r} "
            f"and target {target_ref!r}"
        )
    return matches[0]


def _require_allowed_prefix(path: str, prefixes: tuple[str, ...]) -> None:
    if not any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
        raise PathOutOfScopeError(
            f"repository path {path!r} is outside the allowed prefixes"
        )


def _check_tree_walk(path: str, tree: Mapping[str, RepositoryTreeKind]) -> None:
    parts = path.split("/")
    for depth in range(1, len(parts) + 1):
        entry = "/".join(parts[:depth])
        kind = tree.get(entry)
        if depth < len(parts):
            if kind != "tree":
                raise PathUnsupportedKindError(
                    f"path component {entry!r} is not a directory in the base tree"
                )
        elif kind in {"symlink", "submodule", "tree"}:
            raise PathUnsupportedKindError(
                f"path target {entry!r} is a {kind}, not a regular file"
            )


def resolve_scope(
    scopes: Sequence[RepositoryScope],
    *,
    repository_id: str,
    target_ref: str,
    relative_path: str,
    tree: Mapping[str, RepositoryTreeKind],
) -> RepositoryScope:
    """Resolve one write path against the trusted scope and the immutable tree.

    ``tree`` is the immutable tree of the pinned base revision. The path is
    applied as spelled, with no normalization: it must be under an allowed
    prefix, spell nothing a provider may reinterpret, and end at a regular
    file or at a new file whose parent chain is intact.
    """
    scope = _match_scope(scopes, repository_id=repository_id, target_ref=target_ref)
    try:
        _check_repository_path(relative_path)
    except ValueError as exc:
        raise PathEscapesError(
            f"repository path {relative_path!r} escapes the relative path space"
        ) from exc
    _require_allowed_prefix(relative_path, scope.path_prefixes)
    _check_tree_walk(relative_path, tree)
    return scope


def _payload_bytes(intent: RepositorySourceEffectIntent) -> bytes:
    try:
        return base64.b64decode(intent.payload_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChangeSetValidationError(
            "repository intent payload is not canonical base64"
        ) from exc


class RepositoryEffectTransport:
    """The shared transport for repository effects over the business API.

    One transport binds one repository: the trusted write scopes, the pinned
    adapter profile and the immutable tree at the bound base revision.
    The effect is audit-bound: ``apply_reserved`` matches the exact preview,
    the recorded approval, the contract pin, the source version and the
    audit reservation before the provider is invoked, and every other path
    refuses without a provider call.
    """

    def __init__(
        self,
        provider: RepositoryProviderPort,
        profile: RepositoryAdapterProfile,
        scopes: Sequence[RepositoryScope],
        *,
        repository_id: str,
        base_revision: str,
        base_tree: Mapping[str, RepositoryTreeKind],
        ledger: WriteBackLedger,
    ) -> None:
        if not repository_id or not base_revision:
            raise ValueError("repository_id and base_revision must not be empty")
        self._provider = provider
        self._profile = profile
        self._scopes = tuple(scopes)
        self._repository_id = repository_id
        self._base_revision = base_revision
        self._base_tree = base_tree
        self._ledger = ledger
        self._effects: dict[str, WriteBackAttempt] = {}

    async def read_current(self, change_set: SourceChangeSet) -> SourceSnapshot:
        """Read the bound base state without mutation."""
        self._require_base(change_set)
        return SourceSnapshot(source_version=self._base_revision, fields={})

    async def preview(
        self, change_set: SourceChangeSet, current: SourceSnapshot
    ) -> DryRunObservation:
        """Resolve scope against the base tree before any dry-run diff."""
        self._require_base(change_set)
        self._resolve(self._intent(change_set))
        return preview_observation(change_set, current)

    async def prior_effect(
        self, change_set: SourceChangeSet
    ) -> WriteBackAttempt | None:
        """Return an exactly matching prior effect or reject key reuse."""
        effect = self._effects.get(change_set.idempotency_key)
        if effect is None:
            return None
        return check_prior_effect(effect, change_set)

    async def apply(
        self, change_set: SourceChangeSet, expected_version: str
    ) -> WriteBackAttempt:
        """Refuse: the repository effect is audit-bound and has no reservation."""
        raise ApprovalBindingMismatchError(
            "repository effects require the bound audit reservation before the provider call"
        )

    async def apply_reserved(
        self,
        change_set: SourceChangeSet,
        expected_version: str,
        reservation: AuditReservation,
    ) -> WriteBackAttempt:
        """Match the five pre-effect bindings, then commit through the API.

        The transport matches, in order, the exact preview at the current
        source state, the recorded approval (the durable change set
        registered for this effect), the contract pin (the digest
        recomputed over the in-flight copy), the source version and the
        audit reservation. A substituted actor, connector, target, path,
        base, bytes, digest, policy or approval refuses with
        ``APPROVAL_BINDING_MISMATCH`` and no mutation.
        """
        intent = self._intent(change_set)
        current = SourceSnapshot(source_version=self._base_revision, fields={})
        try:
            require_preview_binding(
                change_set,
                current,
                expected_preview(change_set, current),
                preview_observation(change_set, current),
            )
        except ChangeSetValidationError as exc:
            raise ApprovalBindingMismatchError(
                "preview does not bind the exact approved effect"
            ) from exc
        durable = await self._ledger.get(change_set.tenant_id, change_set.change_set_id)
        if durable is None:
            raise ApprovalBindingMismatchError(
                "no recorded approval exists for this effect"
            )
        if durable != change_set:
            raise ApprovalBindingMismatchError(
                "recorded approval does not bind this effect"
            )
        if durable.change_set_digest != durable.canonical_digest():
            raise ApprovalBindingMismatchError(
                "recorded approval is not canonically digested"
            )
        if change_set.change_set_digest != change_set.canonical_digest():
            raise ApprovalBindingMismatchError(
                "contract pin no longer covers the complete effect"
            )
        if (
            expected_version != change_set.base_source_version
            or change_set.base_source_version != self._base_revision
        ):
            raise ApprovalBindingMismatchError(
                "source version does not match the approved base"
            )
        if (
            not reservation.reservation_id.strip()
            or reservation.change_set_digest != change_set.change_set_digest
            or reservation.effect_key != change_set.idempotency_key
        ):
            raise ApprovalBindingMismatchError(
                "audit reservation is not bound to this approved effect"
            )
        return await self._commit(change_set, intent, reservation)

    async def reconcile(self, change_set: SourceChangeSet) -> ReconciliationObservation:
        """Report the recorded effect, or no effect, at the bound base state."""
        self._require_base(change_set)
        status = (
            WriteBackEffectStatus.APPLIED
            if change_set.idempotency_key in self._effects
            else WriteBackEffectStatus.NO_EFFECT
        )
        return reconciliation_observation(
            change_set,
            SourceSnapshot(source_version=self._base_revision, fields={}),
            status,
        )

    async def _commit(
        self,
        change_set: SourceChangeSet,
        intent: RepositorySourceEffectIntent,
        reservation: AuditReservation,
    ) -> WriteBackAttempt:
        """Profile and scope checks, then the single conditional provider call.

        The conditional commit is followed by the independent readback at
        the actual effect revision; together they establish the evidence
        the durable attempt receipt is bound to.
        """
        if not self._provider.supports_conditional_update:
            raise ProviderLacksConditionalUpdateError(
                "provider is not admitted: it cannot offer the atomic commit condition"
            )
        self._require_profile(intent)
        self._resolve(intent)
        commit = ConditionalCommit(
            repository_id=intent.repository_id,
            target_ref=intent.target_ref,
            relative_path=intent.relative_path,
            base_revision=intent.base_revision,
            expected_file_digest=intent.expected_file_digest,
            expected_executable=intent.expected_executable,
            payload=_payload_bytes(intent),
            executable=intent.executable,
            idempotency_key=change_set.idempotency_key,
        )
        try:
            receipt = await self._provider.commit_file(
                self._profile.business_api_operation, commit
            )
        except RepositoryBusinessRuleRejected as exc:
            raise BusinessRuleRejectedError(exc.detail) from exc
        except BaseStale as exc:
            raise BaseStaleError(str(exc)) from exc
        except FileStateMismatch as exc:
            raise FileStateMismatchError(str(exc)) from exc
        if receipt.business_api_operation != self._profile.business_api_operation:
            raise AdapterProfileIncompleteError(
                "provider acknowledged a different business operation"
            )
        await self._readback(intent, receipt.effect_revision)
        return await self._attempt(change_set, intent, reservation, receipt)

    async def _attempt(
        self,
        change_set: SourceChangeSet,
        intent: RepositorySourceEffectIntent,
        reservation: AuditReservation,
        provider_receipt: RepositoryCommitReceipt,
    ) -> WriteBackAttempt:
        """Bind the effect receipt, map it onto the attempt, persist it.

        The provider effect, the independent readback and the durable
        ``WriteBackAttempt`` are one transaction: any step that fails
        establishes no success. The only evidence of the effect is the
        provider's immutable ``effect_revision``, proven by the readback
        at that revision: an echoed request key, an applied flag, a
        mutable branch name or an invented revision never does.
        """
        request = RepositorySourceEffectRequest(
            schema_version=1, change_set=change_set, intent=intent
        )
        receipt = RepositorySourceEffectReceipt(
            schema_version=1,
            tenant_id=change_set.tenant_id,
            actor=change_set.actor,
            connector_id=change_set.connector_id,
            source_instance_id=change_set.source_instance_id,
            repository_id=intent.repository_id,
            target_ref=intent.target_ref,
            relative_path=intent.relative_path,
            base_revision=intent.base_revision,
            change_set_id=change_set.change_set_id,
            change_set_digest=change_set.change_set_digest,
            idempotency_key=change_set.idempotency_key,
            approval_ref=change_set.authorization.authorization_ref,
            approval_decision_digest=change_set.authorization.decision_digest,
            audit_reservation_id=reservation.reservation_id,
            adapter_contract_digest=intent.adapter_contract_digest,
            effect_revision=provider_receipt.effect_revision,
            readback_sha256=intent.payload_sha256,
            readback_size=intent.payload_size,
            executable=intent.executable,
        )
        try:
            receipt.require_binding(request, reservation)
        except ValueError as exc:
            raise ReceiptUnboundError(
                "the effect receipt is not bound to the approved request"
            ) from exc
        attempt = applied_attempt(
            change_set,
            pre_version=receipt.base_revision,
            post_version=receipt.effect_revision,
            observation_digest=receipt.receipt_digest(),
        )
        try:
            await self._ledger.record_attempt(attempt)
        except Exception as exc:
            raise ReceiptPersistenceFailedError(
                "the durable attempt receipt could not be persisted"
            ) from exc
        self._effects[change_set.idempotency_key] = attempt
        return attempt

    async def _readback(
        self, intent: RepositorySourceEffectIntent, effect_revision: str
    ) -> None:
        """Read the file independently at the actual effect revision.

        The only readback source is the provider's ``read_file``: the
        mutation response body and any ingestion cache are never used. A
        mismatched or unavailable read refuses the effect as uncertain.
        """
        read = await self._provider.read_file(
            intent.repository_id, effect_revision, intent.relative_path
        )
        if read is None:
            raise ReadbackUnavailableError(
                f"no independent read of {intent.relative_path!r} at {effect_revision}"
            )
        try:
            intent.require_readback(read.data, read.executable)
        except ValueError as exc:
            raise ReadbackMismatchError(str(exc)) from exc

    def _require_base(self, change_set: SourceChangeSet) -> None:
        if change_set.base_source_version != self._base_revision:
            raise SourceVersionConflictError(
                "change set base revision does not match the transport's bound tree"
            )

    def _intent(self, change_set: SourceChangeSet) -> RepositorySourceEffectIntent:
        patch = change_set.desired_patch
        if set(patch) != {"repository_effect"} or change_set.field_scope != [
            "repository_effect"
        ]:
            raise ChangeSetValidationError(
                "change set does not carry a repository effect"
            )
        try:
            return RepositorySourceEffectIntent.model_validate(
                patch["repository_effect"]
            )
        except ValidationError as exc:
            raise ChangeSetValidationError(
                "change set carries an invalid repository intent"
            ) from exc

    def _require_profile(self, intent: RepositorySourceEffectIntent) -> None:
        if not self._profile.business_api_operation.strip():
            raise AdapterProfileIncompleteError(
                "adapter profile has no business API operation"
            )
        if self._profile.contract_digest() != intent.adapter_contract_digest:
            raise AdapterProfileIncompleteError(
                "adapter profile is not bound by the intent's contract digest"
            )

    def _resolve(self, intent: RepositorySourceEffectIntent) -> None:
        resolve_scope(
            self._scopes,
            repository_id=intent.repository_id,
            target_ref=intent.target_ref,
            relative_path=intent.relative_path,
            tree=self._base_tree,
        )
