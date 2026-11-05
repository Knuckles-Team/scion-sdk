"""The store registry: named stores with secrets by reference (SCION-CENTRALIZED-CAPABILITIES-R042.6).

A store is one backend plus its connection settings. Its ``credentials`` map
holds secret references only (``env://`` or ``openbao://``), keyed by field
name; a literal value refuses at parse time. The factory resolves the
references when it builds the backend, so a resolved value never sits in a
``StoreConfig``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.storage.base import SecretLiteralError, StoreConfigError

__all__ = [
    "BACKEND_ALIASES",
    "CREDENTIAL_FIELDS",
    "StoreConfig",
    "parse_store",
    "parse_stores",
]

#: Accepted ``backend`` spellings and the backend each one selects.
BACKEND_ALIASES: dict[str, str] = {
    "filesystem": "filesystem",
    "fs": "filesystem",
    "local": "filesystem",
    "s3": "s3",
    "minio": "s3",
    "r2": "s3",
    "gcs": "gcs",
    "gcp": "gcs",
    "google": "gcs",
    "azure": "azure",
    "azure_blob": "azure",
    "abs": "azure",
    "webdav": "webdav",
    "dav": "webdav",
}

#: The credential fields each backend accepts.
CREDENTIAL_FIELDS: dict[str, frozenset[str]] = {
    "filesystem": frozenset(),
    "s3": frozenset({"access_key_id", "secret_access_key", "session_token"}),
    "gcs": frozenset({"service_account_json"}),
    "azure": frozenset({"connection_string"}),
    "webdav": frozenset({"username", "password"}),
}

_FIELDS = frozenset(
    {
        "backend",
        "bucket",
        "endpoint",
        "region",
        "root",
        "project",
        "presign_endpoint",
        "allowed_private_hosts",
        "credentials",
    }
)


@dataclass(frozen=True)
class StoreConfig:
    """One named store: a backend kind plus connection settings and references."""

    name: str
    backend: str
    bucket: str | None = None
    endpoint: str | None = None
    region: str | None = None
    root: str | None = None
    project: str | None = None
    presign_endpoint: str | None = None
    allowed_private_hosts: tuple[str, ...] = ()
    credentials: Mapping[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """The canonical backend kind the ``backend`` alias selects."""
        return BACKEND_ALIASES[self.backend]


def _optional_text(name: str, raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None or isinstance(value, str):
        return value
    raise StoreConfigError(f"store {name!r}: {key} must be a string")


def _credentials(name: str, kind: str, raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise StoreConfigError(f"store {name!r}: credentials must be an object")
    allowed = CREDENTIAL_FIELDS[kind]
    result: dict[str, str] = {}
    for key, reference in raw.items():
        if key not in allowed:
            raise StoreConfigError(f"store {name!r}: unknown credential {key!r}")
        try:
            parse_secret_reference(str(reference))
        except SecretReferenceError:
            raise SecretLiteralError(
                f"store {name!r}: credential {key!r} must be a secret reference"
            ) from None
        result[key] = str(reference)
    return result


def _hosts(name: str, raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str) or not all(isinstance(h, str) for h in raw):
        raise StoreConfigError(f"store {name!r}: allowed_private_hosts is a list")
    return tuple(raw)


def parse_store(name: str, raw: Any) -> StoreConfig:
    """Parse one store declaration.

    Raises:
        StoreConfigError: ``STORAGE_CONFIG_INVALID`` for a malformed entry,
            an unknown backend or an unknown field.
        SecretLiteralError: ``STORAGE_SECRET_LITERAL`` for a literal secret.
    """
    if not isinstance(raw, Mapping):
        raise StoreConfigError(f"store {name!r} must be an object")
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        raise StoreConfigError(f"store {name!r}: unknown field {unknown[0]!r}")
    backend = str(raw.get("backend") or "").lower()
    if backend not in BACKEND_ALIASES:
        raise StoreConfigError(f"store {name!r}: unknown backend {backend!r}")
    kind = BACKEND_ALIASES[backend]
    text = {
        key: _optional_text(name, raw, key)
        for key in ("bucket", "endpoint", "region", "root", "project")
    }
    return StoreConfig(
        name=name,
        backend=backend,
        presign_endpoint=_optional_text(name, raw, "presign_endpoint"),
        allowed_private_hosts=_hosts(name, raw.get("allowed_private_hosts")),
        credentials=_credentials(name, kind, raw.get("credentials")),
        **text,
    )


def parse_stores(raw: Any) -> dict[str, StoreConfig]:
    """Parse a ``{name: declaration}`` mapping into named stores."""
    if not isinstance(raw, Mapping):
        raise StoreConfigError("stores must be an object of named stores")
    return {str(name): parse_store(str(name), entry) for name, entry in raw.items()}
