"""The authority facts a credential resolution is bound to (SCION-CONNECTOR-CONTROL-R035.1).

The host injects a :class:`CredentialAccessBinding` through the authority port
of the provider. A request parameter never supplies one of these facts.
References are opaque; no field holds a secret value.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CredentialAccessBinding",
    "CredentialUseRecord",
    "binding_digest",
    "current_credential_binding",
    "refuse_request_authority",
    "use_credential_binding",
]


class CredentialAccessBinding(BaseModel):
    """One exact, current grant: who may resolve which credential for what."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant: str = Field(min_length=1)
    owner_principal: str = Field(min_length=1)
    requesting_principal: str = Field(min_length=1)
    client: str = Field(min_length=1)
    connector: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    scopes: tuple[str, ...] = ()
    resource: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    grant_ref: str = Field(min_length=1)
    vault_ref: str = Field(min_length=1)
    expires_at_ms: int = Field(ge=0)
    revocation_revision: str = Field(min_length=1)
    token_profile: str = Field(min_length=1)
    profile_revision: str = Field(min_length=1)


class CredentialUseRecord(BaseModel):
    """What is recorded per resolution and per refusal, before resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    binding_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tenant: str
    client: str
    connector: str
    resource: str
    operation: str
    grant_revision: str
    safe_reference: str
    outcome: str


def binding_digest(binding: CredentialAccessBinding) -> str:
    """SHA-256 (64 lowercase hex) of the canonical JSON of ``binding``."""
    canonical = json.dumps(
        binding.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def refuse_request_authority(params: Mapping[str, object]) -> None:
    """Refuse request parameters that try to supply authority facts."""
    named = sorted(set(params) & set(CredentialAccessBinding.model_fields))
    if named:
        raise ValueError(f"request parameters may not supply authority: {named}")


_current: ContextVar[CredentialAccessBinding | None] = ContextVar(
    "scion_credential_binding", default=None
)


def current_credential_binding() -> CredentialAccessBinding | None:
    """The binding the served request context carries, if any."""
    return _current.get()


@contextmanager
def use_credential_binding(
    binding: CredentialAccessBinding | None,
) -> Iterator[None]:
    """Bind ``binding`` for the enclosed block."""
    token = _current.set(binding)
    try:
        yield
    finally:
        _current.reset(token)
