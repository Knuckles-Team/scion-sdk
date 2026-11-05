"""Google Cloud Storage backend (SCION-CENTRALIZED-CAPABILITIES-R042).

A service-account document arrives resolved from a secret reference
(SCION-CENTRALIZED-CAPABILITIES-R042.6). Without one, the platform workload
identity applies. Presigned URLs require signing-capable credentials.

Requires the ``gcs`` extra: ``pip install scion-sdk[storage-gcs]``.
"""

import json
from datetime import timedelta
from typing import Any

from scion_sdk.storage.base import (
    AlreadyExistsError,
    BucketInfo,
    BucketNotEmptyError,
    Metadata,
    MissingDependencyError,
    NotFoundError,
    ObjectInfo,
    ObjectPage,
    StorageError,
    TooLargeError,
    validate_bucket_name,
    validate_key,
)


def _build_client(project: str | None, service_account_json: str | None) -> Any:
    try:
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - exercised via mocked import
        raise MissingDependencyError(
            "The GCS backend requires google-cloud-storage. Install it with "
            "`pip install scion-sdk[storage-gcs]`."
        ) from exc
    if service_account_json:
        info = json.loads(service_account_json)
        return storage.Client.from_service_account_info(info, project=project)
    return storage.Client(project=project)


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 404 or exc.__class__.__name__ == "NotFound"


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "code", None) == 409 or exc.__class__.__name__ == "Conflict"


class GCSBackend:
    """Google Cloud Storage object-store backend."""

    backend_type = "gcs"

    def __init__(
        self,
        project: str | None = None,
        service_account_json: str | None = None,
        client: Any | None = None,
    ):
        """``client`` injects a pre-built storage client (used by tests)."""
        self.client = (
            client
            if client is not None
            else _build_client(project, service_account_json)
        )

    def capabilities(self) -> dict[str, bool]:
        return {
            "presigned_urls": True,
            "object_metadata": True,
            "bucket_location": True,
        }

    def _translate(self, exc: Exception, context: str) -> StorageError:
        if _is_not_found(exc):
            return NotFoundError(f"{context} not found.")
        if _is_conflict(exc):
            return AlreadyExistsError(f"{context} already exists.")
        return StorageError(f"{context}: {type(exc).__name__}")

    # -- buckets -------------------------------------------------------------
    def list_buckets(self) -> list[BucketInfo]:
        try:
            buckets = list(self.client.list_buckets())
        except Exception as exc:
            raise self._translate(exc, "Bucket listing") from exc
        return [self._bucket_info(b) for b in buckets]

    @staticmethod
    def _bucket_info(bucket: Any) -> BucketInfo:
        created = getattr(bucket, "time_created", None)
        return BucketInfo(
            name=bucket.name,
            created=created.isoformat() if created else None,
            location=getattr(bucket, "location", None),
        )

    def create_bucket(self, bucket: str, location: str | None = None) -> BucketInfo:
        validate_bucket_name(bucket)
        try:
            created = self.client.create_bucket(bucket, location=location)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc
        return self._bucket_info(created)

    def delete_bucket(self, bucket: str) -> None:
        handle = self._bucket(bucket)
        try:
            # force=False refuses to delete a non-empty bucket.
            handle.delete(force=False)
        except Exception as exc:
            if _is_conflict(exc):
                raise BucketNotEmptyError(f"Bucket {bucket!r} is not empty.") from exc
            raise self._translate(exc, f"Bucket {bucket!r}") from exc

    def bucket_exists(self, bucket: str) -> bool:
        try:
            return self.client.lookup_bucket(bucket) is not None
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc

    def bucket_info(self, bucket: str) -> BucketInfo:
        try:
            handle = self.client.get_bucket(bucket)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc
        return self._bucket_info(handle)

    def _bucket(self, bucket: str) -> Any:
        return self.client.bucket(bucket)

    # -- objects ---------------------------------------------------------------
    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        delimiter: str | None = None,
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        try:
            iterator = self.client.list_blobs(
                bucket,
                prefix=prefix or None,
                delimiter=delimiter,
                max_results=max_keys,
                page_token=continuation_token,
            )
            blobs = list(iterator)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc
        next_token = getattr(iterator, "next_page_token", None)
        page = ObjectPage(
            next_token=next_token,
            truncated=bool(next_token),
            prefixes=sorted(getattr(iterator, "prefixes", set()) or set()),
        )
        for blob in blobs:
            page.objects.append(self._blob_info(blob))
        return page

    @staticmethod
    def _blob_info(blob: Any) -> ObjectInfo:
        updated = getattr(blob, "updated", None)
        return ObjectInfo(
            key=blob.name,
            size=blob.size or 0,
            etag=getattr(blob, "etag", None),
            last_modified=updated.isoformat() if updated else None,
            content_type=getattr(blob, "content_type", None),
            metadata=dict(getattr(blob, "metadata", None) or {}),
            storage_class=getattr(blob, "storage_class", None),
        )

    def _get_blob(self, bucket: str, key: str) -> Any:
        try:
            blob = self._bucket(bucket).get_blob(key)
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc
        if blob is None:
            raise NotFoundError(f"Object {bucket!r}/{key!r} not found.")
        return blob

    def head_object(self, bucket: str, key: str) -> ObjectInfo:
        return self._blob_info(self._get_blob(bucket, key))

    def get_object(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        blob = self._get_blob(bucket, key)
        size = blob.size or 0
        if max_bytes is not None and size > max_bytes:
            raise TooLargeError(
                f"Object {bucket!r}/{key!r} is {size} bytes, over the "
                f"{max_bytes}-byte read cap."
            )
        try:
            return bytes(blob.download_as_bytes())
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc

    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: Metadata | None = None,
    ) -> ObjectInfo:
        validate_key(key)
        blob = self._bucket(bucket).blob(key)
        if metadata:
            blob.metadata = dict(metadata)
        try:
            blob.upload_from_string(data, content_type=content_type)
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc
        return ObjectInfo(
            key=key,
            size=len(data),
            etag=getattr(blob, "etag", None),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> ObjectInfo:
        validate_key(dst_key)
        blob = self._get_blob(src_bucket, src_key)
        try:
            copied = self._bucket(src_bucket).copy_blob(
                blob, self._bucket(dst_bucket), dst_key
            )
        except Exception as exc:
            raise self._translate(
                exc, f"Copy {src_bucket!r}/{src_key!r} -> {dst_bucket!r}/{dst_key!r}"
            ) from exc
        return self._blob_info(copied)

    def delete_object(self, bucket: str, key: str) -> None:
        blob = self._get_blob(bucket, key)
        try:
            blob.delete()
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, str]:
        return self.head_object(bucket, key).metadata

    def set_object_metadata(
        self, bucket: str, key: str, metadata: Metadata
    ) -> ObjectInfo:
        blob = self._get_blob(bucket, key)
        blob.metadata = dict(metadata or {})
        try:
            blob.patch()
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc
        return self._blob_info(blob)

    def presigned_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 3600
    ) -> str:
        if method.upper() not in ("GET", "PUT"):
            raise StorageError(f"Presign method must be GET or PUT (got {method!r}).")
        blob = self._bucket(bucket).blob(key)
        try:
            return str(
                blob.generate_signed_url(
                    version="v4",
                    expiration=timedelta(seconds=expires_in),
                    method=method.upper(),
                )
            )
        except Exception as exc:
            raise StorageError(
                "Presign failed; signing requires a configured service-account key "
                f"({type(exc).__name__})."
            ) from None
