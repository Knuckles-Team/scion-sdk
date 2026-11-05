"""Local-filesystem object store (SCION-CENTRALIZED-CAPABILITIES-R042) — the zero-infra default.

Layout under a configurable root directory::

    <root>/<bucket>/<key...>          object payloads (keys map to sub-paths)
    <root>/.meta/<bucket>/<key>.json  sidecar: content_type + user metadata

Buckets are the top-level directories of the root (dot-directories are
reserved). This backend gives the full :class:`ObjectStoreBackend` surface
with no cloud credentials, which is what lets the conformance test suite and
local development run for real instead of against mocks.
"""

import hashlib
import json
import mimetypes
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scion_sdk.storage.base import (
    AlreadyExistsError,
    BucketInfo,
    BucketNotEmptyError,
    InvalidNameError,
    Metadata,
    NotFoundError,
    ObjectInfo,
    ObjectPage,
    StorageError,
    TooLargeError,
    UnsupportedOperationError,
    validate_bucket_name,
    validate_key,
)

_META_DIR = ".meta"


class FilesystemBackend:
    """Object-store backend rooted at a local directory."""

    backend_type = "filesystem"

    def __init__(self, root: str):
        if not root:
            raise StorageError("Filesystem backend requires a root directory.")
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> dict[str, bool]:
        return {
            "presigned_urls": False,
            "object_metadata": True,
            "bucket_location": False,
        }

    # -- path helpers ------------------------------------------------------
    def _reject_symlink_components(self, path: Path) -> Path:
        """Return ``path`` only when every existing component is non-symlink.

        Object keys are already lexically constrained by :func:`validate_key`,
        but an existing symlink below the store root could otherwise redirect a
        valid-looking key outside that root.  Refusing symlinks also protects
        the private metadata tree and keeps reads and writes on the same trust
        boundary.

        This is deliberately fail-closed.  A filesystem-backed object store is
        a storage namespace, not a general-purpose symlink traversal API.
        """
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:  # defensive: callers must stay below root
            raise InvalidNameError(
                "Filesystem object path escapes the store root."
            ) from exc

        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise InvalidNameError(
                    "Filesystem object paths must not contain symbolic links."
                )
        return path

    def _bucket_path(self, bucket: str) -> Path:
        return self._reject_symlink_components(self.root / validate_bucket_name(bucket))

    def _object_path(self, bucket: str, key: str) -> Path:
        return self._reject_symlink_components(
            self._bucket_path(bucket) / validate_key(key)
        )

    def _meta_path(self, bucket: str, key: str) -> Path:
        # Validate independently: metadata helpers are also called from paths
        # which already resolved the payload, and must never become a bypass.
        bucket = validate_bucket_name(bucket)
        key = validate_key(key)
        return self._reject_symlink_components(
            self.root / _META_DIR / bucket / f"{key}.json"
        )

    def _require_bucket(self, bucket: str) -> Path:
        path = self._bucket_path(bucket)
        if not path.is_dir():
            raise NotFoundError(f"Bucket {bucket!r} does not exist.")
        return path

    def _require_object(self, bucket: str, key: str) -> Path:
        self._require_bucket(bucket)
        path = self._object_path(bucket, key)
        if not path.is_file():
            raise NotFoundError(f"Object {bucket!r}/{key!r} does not exist.")
        return path

    def _read_sidecar(self, bucket: str, key: str) -> dict[str, Any]:
        meta_path = self._meta_path(bucket, key)
        if meta_path.is_file():
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        return {}

    def _write_sidecar(
        self, bucket: str, key: str, content_type: str | None, metadata: dict[str, str]
    ) -> None:
        meta_path = self._meta_path(bucket, key)
        if not content_type and not metadata:
            if meta_path.is_file():
                meta_path.unlink()
            return
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps({"content_type": content_type, "metadata": metadata}),
            encoding="utf-8",
        )

    def _object_info(self, bucket: str, key: str, path: Path) -> ObjectInfo:
        stat = path.stat()
        sidecar = self._read_sidecar(bucket, key)
        digest_state = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_state.update(chunk)
        digest = digest_state.hexdigest()
        content_type = sidecar.get("content_type") or mimetypes.guess_type(key)[0]
        return ObjectInfo(
            key=key,
            size=stat.st_size,
            etag=digest,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            content_type=content_type,
            metadata=dict(sidecar.get("metadata") or {}),
        )

    # -- buckets -----------------------------------------------------------
    def list_buckets(self) -> list[BucketInfo]:
        buckets = []
        for entry in sorted(self.root.iterdir()):
            if (
                entry.is_dir()
                and not entry.is_symlink()
                and not entry.name.startswith(".")
            ):
                buckets.append(self._bucket_info(entry))
        return buckets

    def _bucket_info(self, path: Path) -> BucketInfo:
        stat = path.stat()
        return BucketInfo(
            name=path.name,
            created=datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat(),
            # The backing path is deployment-private and must not escape via an
            # MCP response, trace, or downstream knowledge-graph record.
            location=None,
        )

    def create_bucket(self, bucket: str, location: str | None = None) -> BucketInfo:
        path = self._bucket_path(bucket)
        if path.exists():
            raise AlreadyExistsError(f"Bucket {bucket!r} already exists.")
        path.mkdir(parents=True)
        return self._bucket_info(path)

    def delete_bucket(self, bucket: str) -> None:
        path = self._require_bucket(bucket)
        if any(path.iterdir()):
            raise BucketNotEmptyError(f"Bucket {bucket!r} is not empty.")
        path.rmdir()
        meta_dir = self.root / _META_DIR / bucket
        if meta_dir.is_dir():
            shutil.rmtree(meta_dir)

    def bucket_exists(self, bucket: str) -> bool:
        return self._bucket_path(bucket).is_dir()

    def bucket_info(self, bucket: str) -> BucketInfo:
        return self._bucket_info(self._require_bucket(bucket))

    # -- objects -----------------------------------------------------------
    def _all_keys(self, bucket: str) -> list[str]:
        bucket_path = self._require_bucket(bucket)
        keys = []
        for path in bucket_path.rglob("*"):
            if path.is_file() and not path.is_symlink():
                keys.append(path.relative_to(bucket_path).as_posix())
        return sorted(keys)

    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        delimiter: str | None = None,
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        keys = [k for k in self._all_keys(bucket) if k.startswith(prefix)]
        if continuation_token:
            keys = [k for k in keys if k > continuation_token]

        page = ObjectPage()
        seen_prefixes: set[str] = set()
        emitted = 0
        last_processed: str | None = None
        bucket_path = self._bucket_path(bucket)
        for key in keys:
            if emitted >= max_keys:
                if delimiter:
                    # Keys under an already-emitted common prefix are folded,
                    # not emitted; they must not re-emit it on the next page.
                    remainder = key[len(prefix) :]
                    if delimiter in remainder:
                        common = prefix + remainder.split(delimiter, 1)[0] + delimiter
                        if common in seen_prefixes:
                            last_processed = key
                            continue
                page.truncated = True
                page.next_token = last_processed
                break
            last_processed = key
            if delimiter:
                remainder = key[len(prefix) :]
                if delimiter in remainder:
                    common = prefix + remainder.split(delimiter, 1)[0] + delimiter
                    if common not in seen_prefixes:
                        seen_prefixes.add(common)
                        emitted += 1
                    continue
            page.objects.append(self._object_info(bucket, key, bucket_path / key))
            emitted += 1
        page.prefixes = sorted(seen_prefixes)
        return page

    def head_object(self, bucket: str, key: str) -> ObjectInfo:
        path = self._require_object(bucket, key)
        return self._object_info(bucket, key, path)

    def get_object(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        path = self._require_object(bucket, key)
        size = path.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise TooLargeError(
                f"Object {bucket!r}/{key!r} is {size} bytes, over the "
                f"{max_bytes}-byte read cap."
            )
        return path.read_bytes()

    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: Metadata | None = None,
    ) -> ObjectInfo:
        self._require_bucket(bucket)
        path = self._object_path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._write_sidecar(bucket, key, content_type, dict(metadata or {}))
        return self._object_info(bucket, key, path)

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> ObjectInfo:
        src = self._require_object(src_bucket, src_key)
        self._require_bucket(dst_bucket)
        dst = self._object_path(dst_bucket, dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        sidecar = self._read_sidecar(src_bucket, src_key)
        self._write_sidecar(
            dst_bucket,
            dst_key,
            sidecar.get("content_type"),
            dict(sidecar.get("metadata") or {}),
        )
        return self._object_info(dst_bucket, dst_key, dst)

    def delete_object(self, bucket: str, key: str) -> None:
        path = self._require_object(bucket, key)
        path.unlink()
        meta_path = self._meta_path(bucket, key)
        if meta_path.is_file():
            meta_path.unlink()
        # Prune now-empty intermediate directories so list/bucket-delete stay clean.
        bucket_path = self._bucket_path(bucket)
        parent = path.parent
        while parent != bucket_path and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, str]:
        self._require_object(bucket, key)
        return dict(self._read_sidecar(bucket, key).get("metadata") or {})

    def set_object_metadata(
        self, bucket: str, key: str, metadata: Metadata
    ) -> ObjectInfo:
        path = self._require_object(bucket, key)
        sidecar = self._read_sidecar(bucket, key)
        self._write_sidecar(
            bucket, key, sidecar.get("content_type"), dict(metadata or {})
        )
        return self._object_info(bucket, key, path)

    def presigned_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 3600
    ) -> str:
        raise UnsupportedOperationError(
            "The filesystem backend cannot mint presigned URLs."
        )
