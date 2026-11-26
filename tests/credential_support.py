"""Shared credential-binding fixtures."""

from __future__ import annotations

from scion_sdk.credentials.binding import CredentialAccessBinding

__all__ = ["make_binding"]


def make_binding(**changes: object) -> CredentialAccessBinding:
    values: dict[str, object] = {
        "tenant": "t1",
        "owner_principal": "owner",
        "requesting_principal": "agent-1",
        "client": "client-a",
        "connector": "vault-agent",
        "provider": "vault",
        "audience": "https://vault.example.test",
        "scopes": ("read",),
        "resource": "secret/app",
        "operation": "read",
        "grant_ref": "grant:1",
        "vault_ref": "vault:1",
        "expires_at_ms": 2_000,
        "revocation_revision": "r1",
        "token_profile": "opaque",
        "profile_revision": "p1",
    }
    return CredentialAccessBinding.model_validate({**values, **changes})
