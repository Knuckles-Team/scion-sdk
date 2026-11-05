"""Azure Blob Storage backend (SCION-CENTRALIZED-CAPABILITIES-R042).

Containers map to buckets. The connection string arrives resolved from a
secret reference (SCION-CENTRALIZED-CAPABILITIES-R042.6); no environment
fallback exists. Presigned URLs are SAS URLs and require an account key in
the connection string.

Requires the ``azure`` extra: ``pip install scion-sdk[storage-azure]``.
"""

from datetime import UTC, datetime, timedelta
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
    StoreConfigError,
    TooLargeError,
    validate_bucket_name,
    validate_key,
)


def _build_client(connection_string: str | None) -> Any:
    if not connection_string:
        raise StoreConfigError(
            "The Azure backend needs a connection_string credential reference."
        )
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:  # pragma: no cover - exercised via mocked import
        raise MissingDependencyError(
            "The Azure backend requires azure-storage-blob. Install it with "
            "`pip install scion-sdk[storage-azure]`."
        ) from exc
    return BlobServiceClient.from_connection_string(connection_string)


def _is_not_found(exc: Exception) -> bool:
    return (
        getattr(exc, "status_code", None) == 404
        or exc.__class__.__name__ == "ResourceNotFoundError"
    )


def _is_conflict(exc: Exception) -> bool:
    return (
        getattr(exc, "status_code", None) == 409
        or exc.__class__.__name__ == "ResourceExistsError"
    )


class AzureBlobBackend:
    """Azure Blob Storage object-store backend (containers as buckets)."""

    backend_type = "azure"

    def __init__(self, connection_string: str | None = None, client: Any | None = None):
        """``client`` injects a pre-built BlobServiceClient (used by tests)."""
        self.client = client if client is not None else _build_client(connection_string)

    def capabilities(self) -> dict[str, bool]:
        return {
            "presigned_urls": True,
            "object_metadata": True,
            "bucket_location": False,
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
            containers = list(self.client.list_containers())
        except Exception as exc:
            raise self._translate(exc, "Container listing") from exc
        results = []
        for container in containers:
            modified = getattr(container, "last_modified", None)
            results.append(
                BucketInfo(
                    name=container.name,
                    created=modified.isoformat() if modified else None,
                )
            )
        return results

    def create_bucket(self, bucket: str, location: str | None = None) -> BucketInfo:
        validate_bucket_name(bucket)
        try:
            self.client.create_container(bucket)
        except Exception as exc:
            raise self._translate(exc, f"Container {bucket!r}") from exc
        return BucketInfo(name=bucket)

    def delete_bucket(self, bucket: str) -> None:
        container = self.client.get_container_client(bucket)
        try:
            # Azure deletes non-empty containers; enforce empty-only parity.
            if next(iter(container.list_blobs()), None) is not None:
                raise BucketNotEmptyError(f"Container {bucket!r} is not empty.")
            container.delete_container()
        except BucketNotEmptyError:
            raise
        except Exception as exc:
            raise self._translate(exc, f"Container {bucket!r}") from exc

    def bucket_exists(self, bucket: str) -> bool:
        try:
            return bool(self.client.get_container_client(bucket).exists())
        except Exception as exc:
            raise self._translate(exc, f"Container {bucket!r}") from exc

    def bucket_info(self, bucket: str) -> BucketInfo:
        container = self.client.get_container_client(bucket)
        try:
            props = container.get_container_properties()
        except Exception as exc:
            raise self._translate(exc, f"Container {bucket!r}") from exc
        modified = getattr(props, "last_modified", None)
        return BucketInfo(
            name=bucket, created=modified.isoformat() if modified else None
        )

    # -- objects ---------------------------------------------------------------
    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        delimiter: str | None = None,
        max_keys: int = 1000,
        continuation_token: str | None = None,
    ) -> ObjectPage:
        container = self.client.get_container_client(bucket)
        try:
            if delimiter:
                iterator = container.walk_blobs(
                    name_starts_with=prefix or None,
                    delimiter=delimiter,
                    results_per_page=max_keys,
                )
            else:
                iterator = container.list_blobs(
                    name_starts_with=prefix or None, results_per_page=max_keys
                )
            pager = iterator.by_page(continuation_token=continuation_token)
            entries = list(next(pager, []))
        except Exception as exc:
            raise self._translate(exc, f"Container {bucket!r}") from exc
        next_token = getattr(pager, "continuation_token", None) or None
        page = ObjectPage(next_token=next_token, truncated=bool(next_token))
        for entry in entries:
            # walk_blobs yields BlobPrefix markers for folded prefixes.
            if entry.__class__.__name__ == "BlobPrefix" or not hasattr(entry, "size"):
                page.prefixes.append(entry.name)
            else:
                page.objects.append(self._blob_info(entry))
        page.prefixes.sort()
        return page

    @staticmethod
    def _blob_info(blob: Any) -> ObjectInfo:
        modified = getattr(blob, "last_modified", None)
        settings = getattr(blob, "content_settings", None)
        return ObjectInfo(
            key=blob.name,
            size=getattr(blob, "size", 0) or 0,
            etag=(getattr(blob, "etag", None) or "").strip('"') or None,
            last_modified=modified.isoformat() if modified else None,
            content_type=getattr(settings, "content_type", None),
            metadata=dict(getattr(blob, "metadata", None) or {}),
            storage_class=getattr(blob, "blob_tier", None),
        )

    def _blob_client(self, bucket: str, key: str) -> Any:
        return self.client.get_blob_client(container=bucket, blob=key)

    def _get_properties(self, bucket: str, key: str) -> Any:
        try:
            return self._blob_client(bucket, key).get_blob_properties()
        except Exception as exc:
            raise self._translate(exc, f"Blob {bucket!r}/{key!r}") from exc

    def head_object(self, bucket: str, key: str) -> ObjectInfo:
        props = self._get_properties(bucket, key)
        info = self._blob_info(props)
        info.key = key
        return info

    def get_object(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        info = self.head_object(bucket, key)
        if max_bytes is not None and info.size > max_bytes:
            raise TooLargeError(
                f"Blob {bucket!r}/{key!r} is {info.size} bytes, over the "
                f"{max_bytes}-byte read cap."
            )
        try:
            return bytes(self._blob_client(bucket, key).download_blob().readall())
        except Exception as exc:
            raise self._translate(exc, f"Blob {bucket!r}/{key!r}") from exc

    def put_object(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
        metadata: Metadata | None = None,
    ) -> ObjectInfo:
        validate_key(key)
        kwargs: dict[str, Any] = {"overwrite": True}
        if metadata:
            kwargs["metadata"] = dict(metadata)
        if content_type:
            try:
                from azure.storage.blob import ContentSettings

                kwargs["content_settings"] = ContentSettings(content_type=content_type)
            except ImportError:
                # Injected-client mode (tests) without the SDK installed:
                # pass the raw content type through.
                kwargs["content_type"] = content_type
        try:
            self._blob_client(bucket, key).upload_blob(data, **kwargs)
        except Exception as exc:
            raise self._translate(exc, f"Blob {bucket!r}/{key!r}") from exc
        return ObjectInfo(
            key=key,
            size=len(data),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> ObjectInfo:
        validate_key(dst_key)
        source_url = self._blob_client(src_bucket, src_key).url
        try:
            self._blob_client(dst_bucket, dst_key).start_copy_from_url(source_url)
        except Exception as exc:
            raise self._translate(
                exc, f"Copy {src_bucket!r}/{src_key!r} -> {dst_bucket!r}/{dst_key!r}"
            ) from exc
        return self.head_object(dst_bucket, dst_key)

    def delete_object(self, bucket: str, key: str) -> None:
        try:
            self._blob_client(bucket, key).delete_blob()
        except Exception as exc:
            raise self._translate(exc, f"Blob {bucket!r}/{key!r}") from exc

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, str]:
        return self.head_object(bucket, key).metadata

    def set_object_metadata(
        self, bucket: str, key: str, metadata: Metadata
    ) -> ObjectInfo:
        try:
            self._blob_client(bucket, key).set_blob_metadata(dict(metadata or {}))
        except Exception as exc:
            raise self._translate(exc, f"Blob {bucket!r}/{key!r}") from exc
        return self.head_object(bucket, key)

    def presigned_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 3600
    ) -> str:
        if method.upper() not in ("GET", "PUT"):
            raise StorageError(f"Presign method must be GET or PUT (got {method!r}).")
        try:
            from azure.storage.blob import BlobSasPermissions, generate_blob_sas
        except ImportError as exc:
            raise MissingDependencyError(
                "SAS generation requires azure-storage-blob. Install it with "
                "`pip install scion-sdk[storage-azure]`."
            ) from exc
        account_key = getattr(self.client.credential, "account_key", None)
        if not account_key:
            raise StorageError(
                "SAS URLs require an account key in the connection string."
            )
        permission = BlobSasPermissions(
            read=method.upper() == "GET", write=method.upper() == "PUT"
        )
        sas = generate_blob_sas(
            account_name=self.client.account_name,
            container_name=bucket,
            blob_name=key,
            account_key=account_key,
            permission=permission,
            expiry=datetime.now(UTC) + timedelta(seconds=expires_in),
        )
        return f"{self._blob_client(bucket, key).url}?{sas}"
