"""One storage backend protocol for every store (SCION-CENTRALIZED-CAPABILITIES-R042.1).

A backend is a thin, bucket-agnostic adapter over one object-storage
technology (local filesystem, S3/S3-compatible, Google Cloud Storage, Azure
Blob). Every backend implements the same :class:`ObjectStoreBackend` protocol
so the MCP tool layer can route any store to any provider, and so a single
conformance test suite can validate every implementation.

Design rules:

- Backends are **bucket-agnostic**: the bucket/container is a per-call
  argument; per-store default buckets are resolved by the tool layer.
- Backends raise :class:`StorageError` subclasses; they never leak
  provider SDK exceptions to callers.
- Capabilities a provider cannot offer (e.g. presigned URLs on the local
  filesystem) raise :class:`UnsupportedOperationError` and are advertised via
  :meth:`ObjectStoreBackend.capabilities`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Metadata = Mapping[str, str]


#: Every refusal code a storage backend, the store registry or the factory emits.
STORAGE_NOT_FOUND = "STORAGE_NOT_FOUND"
STORAGE_EXISTS = "STORAGE_EXISTS"
STORAGE_BUCKET_NOT_EMPTY = "STORAGE_BUCKET_NOT_EMPTY"
STORAGE_UNSUPPORTED = "STORAGE_UNSUPPORTED"
STORAGE_NAME_INVALID = "STORAGE_NAME_INVALID"
STORAGE_DEPENDENCY_MISSING = "STORAGE_DEPENDENCY_MISSING"
STORAGE_TOO_LARGE = "STORAGE_TOO_LARGE"
STORAGE_BACKEND_FAILED = "STORAGE_BACKEND_FAILED"
STORAGE_SECRET_LITERAL = "STORAGE_SECRET_LITERAL"  # nosec B105  # sanitizer:ignore - refusal code constant, not a secret value
STORAGE_CONFIG_INVALID = "STORAGE_CONFIG_INVALID"
STORAGE_EGRESS_REFUSED = "STORAGE_EGRESS_REFUSED"

STORAGE_CODES: frozenset[str] = frozenset(
    {
        STORAGE_NOT_FOUND,
        STORAGE_EXISTS,
        STORAGE_BUCKET_NOT_EMPTY,
        STORAGE_UNSUPPORTED,
        STORAGE_NAME_INVALID,
        STORAGE_DEPENDENCY_MISSING,
        STORAGE_TOO_LARGE,
        STORAGE_BACKEND_FAILED,
        STORAGE_SECRET_LITERAL,
        STORAGE_CONFIG_INVALID,
        STORAGE_EGRESS_REFUSED,
    }
)


class StorageError(Exception):
    """Base error for every storage failure; ``code`` is one of :data:`STORAGE_CODES`.

    The message never carries a credential or an upstream response body.
    """

    code: str = STORAGE_BACKEND_FAILED


class NotFoundError(StorageError):
    """The requested bucket or object does not exist."""

    code = STORAGE_NOT_FOUND


class AlreadyExistsError(StorageError):
    """The bucket (or object, where relevant) already exists."""

    code = STORAGE_EXISTS


class BucketNotEmptyError(StorageError):
    """A bucket delete was refused because the bucket still holds objects."""

    code = STORAGE_BUCKET_NOT_EMPTY


class UnsupportedOperationError(StorageError):
    """The backend does not support the requested capability."""

    code = STORAGE_UNSUPPORTED


class InvalidNameError(StorageError):
    """A bucket name or object key failed validation (e.g. path traversal)."""

    code = STORAGE_NAME_INVALID


class MissingDependencyError(StorageError):
    """The provider library for this backend is not installed.

    The message names the SDK extra that supplies it, e.g.
    ``pip install scion-sdk[storage-s3]``.
    """

    code = STORAGE_DEPENDENCY_MISSING


class TooLargeError(StorageError):
    """An object exceeds the caller's read cap."""

    code = STORAGE_TOO_LARGE


class StoreConfigError(StorageError):
    """A store declaration is malformed or names an unknown backend or field."""

    code = STORAGE_CONFIG_INVALID


class SecretLiteralError(StoreConfigError):
    """A store credential holds a literal value instead of a secret reference."""

    code = STORAGE_SECRET_LITERAL


class EgressRefusedError(StorageError):
    """A storage endpoint failed the egress guard."""

    code = STORAGE_EGRESS_REFUSED


@dataclass
class ObjectInfo:
    """Provider-neutral description of a stored object."""

    key: str
    size: int
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    storage_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "size": self.size,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "content_type": self.content_type,
            "metadata": dict(self.metadata),
            "storage_class": self.storage_class,
        }


@dataclass
class BucketInfo:
    """Provider-neutral description of a bucket/container."""

    name: str
    created: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "created": self.created, "location": self.location}


@dataclass
class ObjectPage:
    """One page of a bucket listing."""

    objects: list[ObjectInfo] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    next_token: str | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "objects": [o.to_dict() for o in self.objects],
            "prefixes": self.prefixes,
            "next_token": self.next_token,
            "truncated": self.truncated,
        }


@runtime_checkable
class ObjectStoreBackend(Protocol):
    """Uniform protocol implemented by every storage backend.

    All ``bucket`` arguments name the bucket/container explicitly; backends
    hold no default bucket. All byte payloads are ``bytes`` — text/base64
    encoding is the tool layer's concern.
    """

    backend_type: str

    def capabilities(self) -> dict[str, bool]:
        """Advertise optional capabilities.

        Keys: ``presigned_urls``, ``object_metadata``, ``bucket_location``.
        """
        ...

    # -- buckets -----------------------------------------------------------
    def list_buckets(self) -> list[BucketInfo]:
        """List all buckets/containers visible to the credentials."""
        ...

    def create_bucket(self, bucket: str, location: str | None = None) -> BucketInfo:
        """Create a bucket. Raises AlreadyExistsError if it exists."""
        ...

    def delete_bucket(self, bucket: str) -> None:
        """Delete an EMPTY bucket. Raises BucketNotEmptyError otherwise."""
        ...

    def bucket_exists(self, bucket: str) -> bool:
        """Return True when the bucket exists."""
        ...

    def bucket_info(self, bucket: str) -> BucketInfo:
        """Describe one bucket. Raises NotFoundError when absent."""
        ...

    # -- objects -----------------------------------------------------------
    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        delimiter: str | None = None,
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        """List objects under ``prefix``, optionally folding at ``delimiter``."""
        ...

    def head_object(self, bucket: str, key: str) -> ObjectInfo:
        """Stat one object without downloading it."""
        ...

    def get_object(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        """Download an object's bytes. Raises TooLargeError when the
        object exceeds ``max_bytes``."""
        ...

    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: Metadata | None = None,
    ) -> ObjectInfo:
        """Upload bytes to ``bucket/key`` (overwrites)."""
        ...

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> ObjectInfo:
        """Server-side (where possible) copy of one object."""
        ...

    def delete_object(self, bucket: str, key: str) -> None:
        """Delete exactly one object. Raises NotFoundError when absent."""
        ...

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, str]:
        """Return the user metadata of one object."""
        ...

    def set_object_metadata(
        self, bucket: str, key: str, metadata: Metadata
    ) -> ObjectInfo:
        """Replace the user metadata of one object."""
        ...

    def presigned_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 3600
    ) -> str:
        """Mint a presigned URL. Raises UnsupportedOperationError when the
        backend cannot presign."""
        ...


def validate_key(key: str) -> str:
    """Validate an object key: non-empty, relative, no traversal segments."""
    if not key or not isinstance(key, str):
        raise InvalidNameError("Object key must be a non-empty string.")
    if key.startswith(("/", "\\")) or "\\" in key:
        raise InvalidNameError(f"Object key must be relative (got {key!r}).")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise InvalidNameError(
            f"Object key {key!r} contains empty or traversal segments."
        )
    return key


def validate_bucket_name(bucket: str) -> str:
    """Validate a bucket name: non-empty, flat, not hidden, no traversal."""
    if not bucket or not isinstance(bucket, str):
        raise InvalidNameError("Bucket name must be a non-empty string.")
    if "/" in bucket or "\\" in bucket:
        raise InvalidNameError(f"Bucket name {bucket!r} must not contain slashes.")
    if bucket.startswith(".") or bucket in ("..",):
        raise InvalidNameError(f"Bucket name {bucket!r} must not start with '.'.")
    return bucket
