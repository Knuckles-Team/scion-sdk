"""Object store and file-share backends for connectors (SCION-CENTRALIZED-CAPABILITIES-R042).

One protocol, :class:`ObjectStoreBackend`, and five backends: S3-compatible,
two hosted cloud object stores, local filesystem and WebDAV. Build a backend
with :func:`create_backend` from a :class:`StoreConfig`; parse a registry of
named stores with :func:`parse_stores`. Provider libraries come from the
extras ``storage-s3``, ``storage-gcs`` and ``storage-azure`` and import lazily.
A connector must not ship its own backend, key validation or presign helper.
"""

from scion_sdk.storage.base import (
    STORAGE_CODES,
    AlreadyExistsError,
    BucketInfo,
    BucketNotEmptyError,
    EgressRefusedError,
    InvalidNameError,
    Metadata,
    MissingDependencyError,
    NotFoundError,
    ObjectInfo,
    ObjectPage,
    ObjectStoreBackend,
    SecretLiteralError,
    StorageError,
    StoreConfigError,
    TooLargeError,
    UnsupportedOperationError,
    validate_bucket_name,
    validate_key,
)
from scion_sdk.storage.config import (
    BACKEND_ALIASES,
    StoreConfig,
    parse_store,
    parse_stores,
)
from scion_sdk.storage.factory import create_backend, endpoint_decision
from scion_sdk.storage.filesystem import FilesystemBackend
from scion_sdk.storage.webdav import DavEntry, WebDavBackend

__all__ = [
    "BACKEND_ALIASES",
    "STORAGE_CODES",
    "AlreadyExistsError",
    "BucketInfo",
    "BucketNotEmptyError",
    "DavEntry",
    "EgressRefusedError",
    "FilesystemBackend",
    "InvalidNameError",
    "Metadata",
    "MissingDependencyError",
    "NotFoundError",
    "ObjectInfo",
    "ObjectPage",
    "ObjectStoreBackend",
    "SecretLiteralError",
    "StorageError",
    "StoreConfig",
    "StoreConfigError",
    "TooLargeError",
    "UnsupportedOperationError",
    "WebDavBackend",
    "create_backend",
    "endpoint_decision",
    "parse_store",
    "parse_stores",
    "validate_bucket_name",
    "validate_key",
]
