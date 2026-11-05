"""S3-compatible object store backend (SCION-CENTRALIZED-CAPABILITIES-R042.2).

Serves any store that speaks the S3 protocol through the ``endpoint`` option.
The access key and secret key arrive resolved from secret references
(SCION-CENTRALIZED-CAPABILITIES-R042.6); no ambient profile or environment
chain is consulted. ``presign_endpoint`` signs URLs for a host that callers
outside the cluster can reach.

Requires the ``storage-s3`` extra: ``pip install scion-sdk[storage-s3]``.
"""

from dataclasses import dataclass
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

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


@dataclass(frozen=True)
class S3Credentials:
    """Resolved S3 keys; never logged, never part of an error."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None = None

    def __repr__(self) -> str:
        return "S3Credentials(<redacted>)"


def _build_client(
    endpoint: str | None,
    region: str | None,
    credentials: S3Credentials,
    verify: str | bool = True,
) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - exercised via mocked import
        raise MissingDependencyError(
            "The S3 backend requires boto3. Install it with "
            "`pip install scion-sdk[storage-s3]`."
        ) from exc
    session = boto3.session.Session(
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
        region_name=region or "us-east-1",
    )
    config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
    return session.client("s3", endpoint_url=endpoint, verify=verify, config=config)


class S3Backend:
    """S3-compatible object store backend."""

    backend_type = "s3"

    def __init__(
        self,
        endpoint: str | None = None,
        region: str | None = None,
        credentials: S3Credentials | None = None,
        presign_endpoint: str | None = None,
        verify: str | bool = True,
        client: Any | None = None,
    ):
        """``client`` injects a pre-built S3 client (used by tests)."""
        if client is not None:
            self.client = client
            self._presign_client = client
            return
        if credentials is None:
            raise StoreConfigError(
                "The S3 backend needs access_key_id and secret_access_key references."
            )
        self.client = _build_client(endpoint, region, credentials, verify)
        self._presign_client = (
            _build_client(presign_endpoint, region, credentials, verify)
            if presign_endpoint
            else self.client
        )

    def capabilities(self) -> dict[str, bool]:
        return {
            "presigned_urls": True,
            "object_metadata": True,
            "bucket_location": True,
        }

    # -- error translation ---------------------------------------------------
    def _translate(self, exc: Exception, context: str) -> StorageError:
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str(response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            return NotFoundError(f"{context} not found.")
        if code in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            return AlreadyExistsError(f"{context} already exists.")
        if code == "BucketNotEmpty":
            return BucketNotEmptyError(f"{context} is not empty.")
        return StorageError(f"{context}: {type(exc).__name__}")

    # -- buckets -------------------------------------------------------------
    def list_buckets(self) -> list[BucketInfo]:
        try:
            response = self.client.list_buckets()
        except Exception as exc:
            raise self._translate(exc, "Bucket listing") from exc
        buckets = []
        for entry in response.get("Buckets", []):
            created = entry.get("CreationDate")
            buckets.append(
                BucketInfo(
                    name=entry["Name"],
                    created=created.isoformat() if created else None,
                )
            )
        return buckets

    def create_bucket(self, bucket: str, location: str | None = None) -> BucketInfo:
        validate_bucket_name(bucket)
        kwargs: dict[str, Any] = {"Bucket": bucket}
        if location and location != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": location}
        try:
            self.client.create_bucket(**kwargs)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc
        return BucketInfo(name=bucket, location=location)

    def delete_bucket(self, bucket: str) -> None:
        try:
            self.client.delete_bucket(Bucket=bucket)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc

    def bucket_exists(self, bucket: str) -> bool:
        try:
            self.client.head_bucket(Bucket=bucket)
            return True
        except Exception as exc:
            translated = self._translate(exc, f"Bucket {bucket!r}")
            if isinstance(translated, NotFoundError):
                return False
            raise translated from exc

    def bucket_info(self, bucket: str) -> BucketInfo:
        try:
            response = self.client.get_bucket_location(Bucket=bucket)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc
        return BucketInfo(
            name=bucket, location=response.get("LocationConstraint") or "us-east-1"
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
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": max_keys,
        }
        if delimiter:
            kwargs["Delimiter"] = delimiter
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        try:
            response = self.client.list_objects_v2(**kwargs)
        except Exception as exc:
            raise self._translate(exc, f"Bucket {bucket!r}") from exc
        page = ObjectPage(
            next_token=response.get("NextContinuationToken"),
            truncated=bool(response.get("IsTruncated", False)),
            prefixes=[p["Prefix"] for p in response.get("CommonPrefixes", [])],
        )
        for entry in response.get("Contents", []):
            modified = entry.get("LastModified")
            page.objects.append(
                ObjectInfo(
                    key=entry["Key"],
                    size=entry.get("Size", 0),
                    etag=(entry.get("ETag") or "").strip('"') or None,
                    last_modified=modified.isoformat() if modified else None,
                    storage_class=entry.get("StorageClass"),
                )
            )
        return page

    def head_object(self, bucket: str, key: str) -> ObjectInfo:
        try:
            response = self.client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc
        modified = response.get("LastModified")
        return ObjectInfo(
            key=key,
            size=response.get("ContentLength", 0),
            etag=(response.get("ETag") or "").strip('"') or None,
            last_modified=modified.isoformat() if modified else None,
            content_type=response.get("ContentType"),
            metadata=dict(response.get("Metadata") or {}),
            storage_class=response.get("StorageClass"),
        )

    def get_object(self, bucket: str, key: str, max_bytes: int | None = None) -> bytes:
        info = self.head_object(bucket, key)
        if max_bytes is not None and info.size > max_bytes:
            raise TooLargeError(
                f"Object {bucket!r}/{key!r} is {info.size} bytes, over the "
                f"{max_bytes}-byte read cap."
            )
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
            return bytes(response["Body"].read())
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
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        if metadata:
            kwargs["Metadata"] = dict(metadata)
        try:
            response = self.client.put_object(**kwargs)
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc
        return ObjectInfo(
            key=key,
            size=len(data),
            etag=(response.get("ETag") or "").strip('"') or None,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    def copy_object(
        self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str
    ) -> ObjectInfo:
        validate_key(dst_key)
        try:
            self.client.copy_object(
                Bucket=dst_bucket,
                Key=dst_key,
                CopySource={"Bucket": src_bucket, "Key": src_key},
            )
        except Exception as exc:
            raise self._translate(
                exc, f"Copy {src_bucket!r}/{src_key!r} -> {dst_bucket!r}/{dst_key!r}"
            ) from exc
        return self.head_object(dst_bucket, dst_key)

    def delete_object(self, bucket: str, key: str) -> None:
        # S3 deletes are idempotent and return 204 for missing keys; check
        # first so callers get a consistent NotFoundError across backends.
        self.head_object(bucket, key)
        try:
            self.client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc

    def get_object_metadata(self, bucket: str, key: str) -> dict[str, str]:
        return self.head_object(bucket, key).metadata

    def set_object_metadata(
        self, bucket: str, key: str, metadata: Metadata
    ) -> ObjectInfo:
        info = self.head_object(bucket, key)
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "CopySource": {"Bucket": bucket, "Key": key},
            "Metadata": dict(metadata or {}),
            "MetadataDirective": "REPLACE",
        }
        if info.content_type:
            kwargs["ContentType"] = info.content_type
        try:
            self.client.copy_object(**kwargs)
        except Exception as exc:
            raise self._translate(exc, f"Object {bucket!r}/{key!r}") from exc
        return self.head_object(bucket, key)

    def presigned_url(
        self, bucket: str, key: str, method: str = "GET", expires_in: int = 3600
    ) -> str:
        operations = {"GET": "get_object", "PUT": "put_object"}
        operation = operations.get(method.upper())
        if operation is None:
            raise StorageError(f"Presign method must be GET or PUT (got {method!r}).")
        try:
            return str(
                self._presign_client.generate_presigned_url(
                    operation,
                    Params={"Bucket": bucket, "Key": key},
                    ExpiresIn=expires_in,
                )
            )
        except Exception as exc:
            raise self._translate(exc, f"Presign {bucket!r}/{key!r}") from exc
