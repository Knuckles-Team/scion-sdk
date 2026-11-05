"""WebDAV file-share backend (SCION-CENTRALIZED-CAPABILITIES-R042.5).

A bucket is one collection directly under the client root, and an object key
is a path inside it. User metadata and presigned URLs refuse with
``STORAGE_UNSUPPORTED``: a file share keeps neither. The path methods of
:class:`~scion_sdk.storage.webdav_client.DavClient` stay public for a consumer
that addresses the share by path.
"""

from __future__ import annotations

from scion_sdk.storage.base import (
    BucketInfo,
    BucketNotEmptyError,
    Metadata,
    NotFoundError,
    ObjectInfo,
    ObjectPage,
    TooLargeError,
    UnsupportedOperationError,
    validate_bucket_name,
    validate_key,
)
from scion_sdk.storage.webdav_client import DavClient, DavEntry

__all__ = ["DavEntry", "WebDavBackend"]

#: Most entries a recursive listing walks before it stops.
_MAX_WALK = 10_000


def _info(key: str, entry: DavEntry) -> ObjectInfo:
    return ObjectInfo(
        key=key,
        size=entry.size,
        etag=entry.etag,
        last_modified=entry.last_modified,
        content_type=entry.content_type,
    )


class _DavBuckets(DavClient):
    """Bucket operations: collections directly under the root."""

    def list_buckets(self) -> list[BucketInfo]:
        return [
            BucketInfo(name=entry.name, created=entry.last_modified)
            for entry in self.propfind("", 1)
            if entry.is_collection and entry.path
        ]

    def create_bucket(self, bucket: str, location: str | None = None) -> BucketInfo:
        self.make_collection(validate_bucket_name(bucket), exist_ok=False)
        return BucketInfo(name=bucket)

    def delete_bucket(self, bucket: str) -> None:
        children = [
            e
            for e in self.propfind(validate_bucket_name(bucket), 1)
            if e.path != bucket
        ]
        if children:
            raise BucketNotEmptyError(f"Bucket {bucket!r} is not empty.")
        self.delete_path(bucket)

    def bucket_exists(self, bucket: str) -> bool:
        try:
            self.bucket_info(bucket)
        except NotFoundError:
            return False
        return True

    def bucket_info(self, bucket: str) -> BucketInfo:
        entries = self.propfind(validate_bucket_name(bucket), 0)
        if not entries or not entries[0].is_collection:
            raise NotFoundError(f"Bucket {bucket!r} not found.")
        return BucketInfo(name=bucket, created=entries[0].last_modified)


class WebDavBackend(_DavBuckets):
    """The storage protocol over one WebDAV collection root."""

    backend_type = "webdav"

    def capabilities(self) -> dict[str, bool]:
        return {
            "presigned_urls": False,
            "object_metadata": False,
            "bucket_location": False,
        }

    def _walk(self, bucket: str, folder: str, recursive: bool) -> list[DavEntry]:
        pending: list[str] = [f"{bucket}/{folder}".rstrip("/")]
        found: list[DavEntry] = []
        while pending and len(found) < _MAX_WALK:
            current = pending.pop()
            for entry in self.propfind(current, 1):
                if entry.path == current.strip("/"):
                    continue
                found.append(entry)
                if recursive and entry.is_collection:
                    pending.append(entry.path)
        return found

    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        delimiter: str | None = None,
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        folder = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        try:
            entries = self._walk(validate_bucket_name(bucket), folder, delimiter != "/")
        except NotFoundError:
            entries = []
        cut = len(bucket) + 1
        keyed = sorted(
            (e.path[cut:], e) for e in entries if e.path[cut:].startswith(prefix)
        )
        page = ObjectPage()
        offset = int(continuation_token or 0)
        window = keyed[offset : offset + max_keys]
        for key, entry in window:
            if entry.is_collection:
                if delimiter == "/":
                    page.prefixes.append(f"{key}/")
            else:
                page.objects.append(_info(key, entry))
        if offset + max_keys < len(keyed):
            page.truncated, page.next_token = True, str(offset + max_keys)
        return page

    def head_object(self, bucket: str, key: str) -> ObjectInfo:
        path = f"{validate_bucket_name(bucket)}/{validate_key(key)}"
        entries = self.propfind(path, 0)
        if not entries or entries[0].is_collection:
            raise NotFoundError(f"Object {bucket!r}/{key!r} not found.")
        return _info(key, entries[0])

    def get_object(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        path = f"{validate_bucket_name(bucket)}/{validate_key(key)}"
        try:
            return self.read_path(path, max_bytes)
        except TooLargeError:
            raise TooLargeError(
                f"Object {bucket!r}/{key!r} is over the {max_bytes}-byte read cap."
            ) from None

    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: Metadata | None = None,
    ) -> ObjectInfo:
        path = f"{validate_bucket_name(bucket)}/{validate_key(key)}"
        self.make_collection(bucket)
        self.write_path(path, data, content_type)
        return ObjectInfo(key=key, size=len(data), content_type=content_type)

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> ObjectInfo:
        source = f"{validate_bucket_name(src_bucket)}/{validate_key(src_key)}"
        target = f"{validate_bucket_name(dst_bucket)}/{validate_key(dst_key)}"
        parent = target.rsplit("/", 1)[0]
        parts = parent.split("/")
        for index in range(1, len(parts) + 1):
            self.make_collection("/".join(parts[:index]))
        self.copy_path(source, target, overwrite=True)
        return self.head_object(dst_bucket, dst_key)

    def delete_object(self, bucket: str, key: str) -> None:
        self.head_object(bucket, key)
        self.delete_path(f"{bucket}/{key}")

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, str]:
        raise UnsupportedOperationError("A WebDAV share keeps no user metadata.")

    def set_object_metadata(
        self, bucket: str, key: str, metadata: Metadata
    ) -> ObjectInfo:
        raise UnsupportedOperationError("A WebDAV share keeps no user metadata.")

    def presigned_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 3600
    ) -> str:
        raise UnsupportedOperationError("A WebDAV share cannot presign a URL.")
