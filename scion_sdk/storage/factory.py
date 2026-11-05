"""Build a backend from a store declaration (SCION-CENTRALIZED-CAPABILITIES-R042.6, R042.7).

The factory is the one place that turns a :class:`StoreConfig` into a live
backend. It resolves the store's secret references, passes every endpoint
through the egress guard, and imports provider libraries lazily so the base
install carries none of them.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from scion_sdk.credentials.resolution import resolve_secret_reference
from scion_sdk.credentials.resolver import CredentialResolver
from scion_sdk.http.egress_validation import validate_base_url_resolved
from scion_sdk.storage.base import (
    EgressRefusedError,
    ObjectStoreBackend,
    StoreConfigError,
)
from scion_sdk.storage.config import BACKEND_ALIASES, StoreConfig

if TYPE_CHECKING:
    from scion_sdk.tls.profile import ResolvedTLSProfile

__all__ = ["create_backend", "endpoint_decision"]


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def endpoint_decision(url: str, allowed_private_hosts: tuple[str, ...] = ()) -> None:
    """Refuse an endpoint outside the egress policy (R042.7).

    A host listed in ``allowed_private_hosts`` may resolve to a private
    address and may use plain ``http``; loopback may use plain ``http``.

    Raises:
        EgressRefusedError: ``STORAGE_EGRESS_REFUSED``.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not host:
        raise EgressRefusedError("storage endpoint must be an http or https URL")
    if parts.username or parts.password:
        raise EgressRefusedError("storage endpoint must not carry credentials")
    allowed = host in {h.lower() for h in allowed_private_hosts}
    if parts.scheme == "http" and not (allowed or _is_loopback(host)):
        raise EgressRefusedError("plain http is limited to loopback and allowed hosts")
    if allowed:
        return
    decision = validate_base_url_resolved(url, allow_loopback=_is_loopback(host))
    if not decision.allowed:
        raise EgressRefusedError("storage endpoint is outside the egress policy")


def _secret(
    store: StoreConfig, key: str, resolver: CredentialResolver | None
) -> str | None:
    reference = store.credentials.get(key)
    if reference is None:
        return None
    try:
        return resolve_secret_reference(reference, resolver)
    except Exception:
        raise StoreConfigError(
            f"store {store.name!r}: credential {key!r} is unavailable"
        ) from None


def _check_endpoints(store: StoreConfig) -> None:
    for url in (store.endpoint, store.presign_endpoint):
        if url:
            endpoint_decision(url, store.allowed_private_hosts)


def _filesystem(store: StoreConfig) -> ObjectStoreBackend:
    from scion_sdk.storage.filesystem import FilesystemBackend

    if not store.root:
        raise StoreConfigError(f"store {store.name!r}: filesystem needs a root")
    return FilesystemBackend(root=store.root)


def _s3(
    store: StoreConfig,
    resolver: CredentialResolver | None,
    tls: ResolvedTLSProfile | None,
) -> ObjectStoreBackend:
    from scion_sdk.storage.s3 import S3Backend, S3Credentials

    key_id = _secret(store, "access_key_id", resolver)
    secret = _secret(store, "secret_access_key", resolver)
    if not key_id or not secret:
        raise StoreConfigError(
            f"store {store.name!r}: s3 needs access_key_id and secret_access_key"
        )
    verify: str | bool = True
    if tls is not None and tls.ca_bundle_path is not None:
        verify = str(tls.ca_bundle_path)
    return S3Backend(
        endpoint=store.endpoint,
        region=store.region,
        credentials=S3Credentials(
            key_id, secret, _secret(store, "session_token", resolver)
        ),
        presign_endpoint=store.presign_endpoint,
        verify=verify,
    )


def _webdav(
    store: StoreConfig,
    resolver: CredentialResolver | None,
    tls: ResolvedTLSProfile | None,
) -> ObjectStoreBackend:
    from scion_sdk.storage.webdav import WebDavBackend

    if not store.endpoint:
        raise StoreConfigError(f"store {store.name!r}: webdav needs an endpoint")
    return WebDavBackend(
        store.endpoint,
        username=_secret(store, "username", resolver),
        password=_secret(store, "password", resolver),
        tls=tls,
        allowed_private_hosts=store.allowed_private_hosts,
    )


def create_backend(
    store: StoreConfig,
    *,
    resolver: CredentialResolver | None = None,
    tls: ResolvedTLSProfile | None = None,
) -> ObjectStoreBackend:
    """Instantiate the backend a :class:`StoreConfig` names.

    Raises:
        StoreConfigError: a required setting or credential is missing.
        EgressRefusedError: an endpoint fails the egress guard.
        MissingDependencyError: the provider library is not installed.
    """
    if store.backend not in BACKEND_ALIASES:
        raise StoreConfigError(
            f"store {store.name!r}: unknown backend {store.backend!r}"
        )
    _check_endpoints(store)
    kind = store.kind
    if kind == "filesystem":
        return _filesystem(store)
    if kind == "s3":
        return _s3(store, resolver, tls)
    if kind == "webdav":
        return _webdav(store, resolver, tls)
    if kind == "gcs":
        from scion_sdk.storage.gcs import GCSBackend

        return GCSBackend(
            project=store.project,
            service_account_json=_secret(store, "service_account_json", resolver),
        )
    from scion_sdk.storage.azure_blob import AzureBlobBackend

    return AzureBlobBackend(
        connection_string=_secret(store, "connection_string", resolver)
    )
