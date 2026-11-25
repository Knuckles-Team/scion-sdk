"""The shared repository-effect transport over the provider's business API.

SCION-GOVERNED-WRITEBACK-R004.4 and R004.6. ``RepositoryEffectTransport``
implements ``WriteBackTransport`` over the ``RepositoryProviderPort``: the
pinned adapter profile must be complete and bound by the intent's
``adapter_contract_digest`` before any provider call, and the write path is
resolved against the trusted scope and the immutable base tree at preview
time and again at effect time. A provider business-rule rejection is final
for the request; no other path is tried.
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

from scion_sdk.ports.repository_provider import (
    RepositoryBusinessRuleRejected,
    RepositoryCommitRequest,
    RepositoryProviderPort,
    RepositoryTreeKind,
)
from scion_sdk.ports.writeback_models import DryRunObservation, SourceSnapshot
from scion_sdk.writeback._contract_json import _decode_contract_json
from scion_sdk.writeback._transport_common import (
    applied_attempt,
    check_prior_effect,
    digest,
    preview_observation,
    reconciliation_observation,
)
from scion_sdk.writeback.errors import (
    ChangeSetValidationError,
    SourceVersionConflictError,
    WriteBackError,
)
from scion_sdk.writeback.repository_effect import (
    RepositorySourceEffectIntent,
    _check_repository_path,
)

__all__ = [
    "REPOSITORY_WRITE_SCOPES",
    "AdapterProfileIncompleteError",
    "BusinessRuleRejectedError",
    "PathEscapesError",
    "PathOutOfScopeError",
    "PathUnsupportedKindError",
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


class BusinessRuleRejectedError(RepositoryEffectRefused):
    """The provider's business API rejected the effect for a business reason."""

    code = "BUSINESS_RULE_REJECTED"


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
    Scope and profile refusals raise before any provider call; the provider
    is invoked through ``commit_file`` and nothing else.
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
    ) -> None:
        if not repository_id or not base_revision:
            raise ValueError("repository_id and base_revision must not be empty")
        self._provider = provider
        self._profile = profile
        self._scopes = tuple(scopes)
        self._repository_id = repository_id
        self._base_revision = base_revision
        self._base_tree = base_tree
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
        """Resolve scope and profile, then commit through the business API."""
        self._require_base(change_set)
        if expected_version != self._base_revision:
            raise SourceVersionConflictError(
                "expected version does not match the bound base revision"
            )
        intent = self._intent(change_set)
        self._require_profile(intent)
        self._resolve(intent)
        commit = RepositoryCommitRequest(
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
        if receipt.business_api_operation != self._profile.business_api_operation:
            raise AdapterProfileIncompleteError(
                "provider acknowledged a different business operation"
            )
        attempt = applied_attempt(
            change_set,
            pre_version=self._base_revision,
            post_version=receipt.effect_revision,
            observation_digest=digest(
                {
                    "business_api_operation": receipt.business_api_operation,
                    "effect_revision": receipt.effect_revision,
                }
            ),
        )
        self._effects[change_set.idempotency_key] = attempt
        return attempt

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
