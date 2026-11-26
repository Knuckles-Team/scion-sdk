"""A credential provider scoped to one connector's identity.

:class:`~scion_sdk.credentials.resolver.CredentialResolver` resolves
a parsed reference to its value with no notion of *who* is asking.
:class:`CredentialProvider` (SCION-CONNECTOR-CONTROL-R035) wraps a resolver with
the requesting connector's own identity: an ``openbao://`` reference whose
path is not that connector's own is refused before any resolver call, and the
resolved value is returned once per call -- never retained, logged, or
echoed by this provider.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from scion_sdk.credentials.binding import (
    CredentialAccessBinding,
    CredentialUseRecord,
    binding_digest,
    current_credential_binding,
)
from scion_sdk.credentials.references import SecretReference
from scion_sdk.credentials.resolver import (
    CredentialResolver,
    CredentialUnavailableError,
)

__all__ = [
    "CredentialAuthorityPort",
    "CredentialProvider",
    "CredentialRefused",
    "CredentialScopeError",
    "NoCredential",
]

_T = TypeVar("_T")


class CredentialRefused(PermissionError):
    """A resolution was refused; ``reason`` is one closed code and never a value."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"credential refused: {reason}")
        self.reason = reason


class CredentialScopeError(CredentialRefused):
    """A reference's scope does not match the requesting connector."""

    def __init__(self, message: str = "") -> None:
        super().__init__("scope_denied")
        if message:
            self.args = (message,)


class CredentialAuthorityPort(Protocol):
    """The host's verdict on one binding: ``"ok"`` or a closed refusal reason."""

    def verify(self, binding: CredentialAccessBinding) -> str: ...


@dataclass(frozen=True)
class CredentialProvider:
    """Resolves one secret reference, scoped to ``connector``'s identity."""

    resolver: CredentialResolver
    connector: str
    authority: CredentialAuthorityPort | None = None
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000
    records: list[CredentialUseRecord] = field(default_factory=list)

    def get_secret(
        self,
        reference: SecretReference,
        binding: CredentialAccessBinding | None = None,
    ) -> str:
        """Return the resolved secret value for ``reference``.

        ``env://`` references are process-wide and always in scope.
        ``openbao://mount/path#field`` references must have ``path`` equal
        to, or nested under, this provider's own ``connector`` (the
        convention a shared mount such as ``apps`` uses to separate
        connectors, e.g. ``openbao://apps/freshrss-agent#TOKEN``).

        Raises:
            CredentialScopeError: an ``openbao://`` reference's path is not
                ``self.connector`` or a path nested under it.
            CredentialUnavailableError: the underlying resolver could not
                resolve the reference (raised by ``self.resolver``).
        """
        if self.authority is not None:
            return self._bound(reference, binding)
        in_scope = reference.path == self.connector or reference.path.startswith(
            f"{self.connector}/"
        )
        if reference.scheme == "openbao" and not in_scope:
            raise CredentialScopeError(
                "secret reference is outside the requesting connector's scope"
            )
        return self.resolver.resolve(reference)

    def inject(
        self,
        reference: SecretReference,
        send: Callable[[str], _T],
        binding: CredentialAccessBinding | None = None,
    ) -> _T:
        """Resolve into ``send`` only; the value is not retained afterwards."""
        value = self.get_secret(reference, binding)
        try:
            return send(value)
        finally:
            del value

    def _bound(
        self, reference: SecretReference, explicit: CredentialAccessBinding | None
    ) -> str:
        binding = explicit or current_credential_binding()
        if binding is None:
            raise CredentialRefused("binding_mismatch")
        reason = self._refusal(reference, binding)
        self._record(reference, binding, reason or "resolved")
        if reason is not None:
            if reason == "scope_denied":
                raise CredentialScopeError(
                    "secret reference is outside the requesting connector's scope"
                )
            raise CredentialRefused(reason)
        try:
            return self.resolver.resolve(reference)
        except CredentialUnavailableError:
            raise CredentialRefused("credential_unavailable") from None

    def _refusal(
        self, reference: SecretReference, binding: CredentialAccessBinding
    ) -> str | None:
        if self.authority is None:
            return "authority_unavailable"
        if binding.connector != self.connector:
            return "binding_mismatch"
        if binding.expires_at_ms <= self.clock_ms():
            return "grant_expired"
        try:
            verdict = self.authority.verify(binding)
        except Exception:
            return "authority_unavailable"
        if verdict != "ok":
            return verdict
        in_scope = reference.path == self.connector or reference.path.startswith(
            f"{self.connector}/"
        )
        if reference.scheme == "openbao" and not in_scope:
            return "scope_denied"
        return None

    def _record(
        self, reference: SecretReference, binding: CredentialAccessBinding, outcome: str
    ) -> None:
        self.records.append(
            CredentialUseRecord(
                binding_digest=binding_digest(binding),
                tenant=binding.tenant,
                client=binding.client,
                connector=binding.connector,
                resource=binding.resource,
                operation=binding.operation,
                grant_revision=binding.revocation_revision,
                safe_reference=reference.target,
                outcome=outcome,
            )
        )


@dataclass(frozen=True)
class NoCredential:
    """Keyless sentinel: present, contributes no auth material (SCION-CONNECTOR-CONTROL-R014.9)."""

    type_name: str = "none"

    def is_present(self) -> bool:
        return True

    def headers(self) -> dict[str, str]:
        return {}
