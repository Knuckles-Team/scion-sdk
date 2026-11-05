"""S3 backend tests against an injected fake client (no boto3 required).

The fake mimics the exact response shapes botocore returns, including
``ClientError``-style exceptions carrying ``.response["Error"]["Code"]``, so
the backend's request building and error translation are tested for real.
A moto-backed integration path is available via the ``test-s3`` extra.
"""

import io
from datetime import UTC, datetime

import pytest

from scion_sdk.storage.base import (
    AlreadyExistsError,
    NotFoundError,
    StorageError,
)
from scion_sdk.storage.s3 import S3Backend

NOW = datetime(2026, 6, 11, tzinfo=UTC)


class FakeClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    """In-memory stand-in mirroring the boto3 S3 client surface we use."""

    def __init__(self):
        self.buckets: dict[str, dict[str, dict]] = {}

    def _bucket(self, name):
        if name not in self.buckets:
            raise FakeClientError("NoSuchBucket")
        return self.buckets[name]

    def _object(self, bucket, key):
        objects = self._bucket(bucket)
        if key not in objects:
            raise FakeClientError("NoSuchKey")
        return objects[key]

    # -- buckets --
    def list_buckets(self):
        return {
            "Buckets": [{"Name": n, "CreationDate": NOW} for n in sorted(self.buckets)]
        }

    def create_bucket(self, Bucket, **kwargs):
        if Bucket in self.buckets:
            raise FakeClientError("BucketAlreadyOwnedByYou")
        self.buckets[Bucket] = {}
        return {}

    def delete_bucket(self, Bucket):
        if self._bucket(Bucket):
            raise FakeClientError("BucketNotEmpty")
        del self.buckets[Bucket]

    def head_bucket(self, Bucket):
        self._bucket(Bucket)
        return {}

    def get_bucket_location(self, Bucket):
        self._bucket(Bucket)
        return {"LocationConstraint": "eu-west-1"}

    # -- objects --
    def list_objects_v2(self, Bucket, Prefix="", MaxKeys=1000, **kwargs):
        keys = sorted(k for k in self._bucket(Bucket) if k.startswith(Prefix))
        start = kwargs.get("ContinuationToken")
        if start:
            keys = [k for k in keys if k > start]
        delimiter = kwargs.get("Delimiter")
        contents, prefixes = [], []
        for key in keys[:MaxKeys]:
            if delimiter and delimiter in key[len(Prefix) :]:
                common = Prefix + key[len(Prefix) :].split(delimiter, 1)[0] + delimiter
                if common not in prefixes:
                    prefixes.append(common)
                continue
            obj = self.buckets[Bucket][key]
            contents.append(
                {
                    "Key": key,
                    "Size": len(obj["Body"]),
                    "ETag": '"abc123"',
                    "LastModified": NOW,
                    "StorageClass": "STANDARD",
                }
            )
        truncated = len(keys) > MaxKeys
        response = {
            "Contents": contents,
            "CommonPrefixes": [{"Prefix": p} for p in prefixes],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = keys[MaxKeys - 1]
        return response

    def head_object(self, Bucket, Key):
        obj = self._object(Bucket, Key)
        return {
            "ContentLength": len(obj["Body"]),
            "ETag": '"abc123"',
            "LastModified": NOW,
            "ContentType": obj.get("ContentType"),
            "Metadata": obj.get("Metadata", {}),
        }

    def get_object(self, Bucket, Key):
        obj = self._object(Bucket, Key)
        return {"Body": io.BytesIO(obj["Body"])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self._bucket(Bucket)[Key] = {
            "Body": Body,
            "ContentType": kwargs.get("ContentType"),
            "Metadata": kwargs.get("Metadata", {}),
        }
        return {"ETag": '"abc123"'}

    def copy_object(self, Bucket, Key, CopySource, **kwargs):
        src = self._object(CopySource["Bucket"], CopySource["Key"])
        copied = dict(src)
        if kwargs.get("MetadataDirective") == "REPLACE":
            copied["Metadata"] = kwargs.get("Metadata", {})
            if kwargs.get("ContentType"):
                copied["ContentType"] = kwargs["ContentType"]
        self._bucket(Bucket)[Key] = copied
        return {}

    def delete_object(self, Bucket, Key):
        self._bucket(Bucket).pop(Key, None)
        return {}

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return (
            f"https://example.com/{Params['Bucket']}/{Params['Key']}"
            f"?op={operation}&expires={ExpiresIn}"
        )


@pytest.fixture
def backend():
    return S3Backend(client=FakeS3Client())


def test_bucket_lifecycle(backend):
    backend.create_bucket("alpha")
    assert backend.bucket_exists("alpha")
    assert not backend.bucket_exists("ghost")
    assert [b.name for b in backend.list_buckets()] == ["alpha"]
    assert backend.bucket_info("alpha").location == "eu-west-1"
    with pytest.raises(AlreadyExistsError):
        backend.create_bucket("alpha")
    backend.delete_bucket("alpha")


def test_delete_non_empty_bucket_translates(backend):
    backend.create_bucket("alpha")
    backend.put_object("alpha", "k", b"x")
    with pytest.raises(StorageError):
        backend.delete_bucket("alpha")


def test_object_roundtrip(backend):
    backend.create_bucket("data")
    info = backend.put_object(
        "data",
        "a/b.txt",
        b"hello",
        content_type="text/plain",
        metadata={"team": "core"},
    )
    assert info.size == 5
    assert info.etag == "abc123"
    head = backend.head_object("data", "a/b.txt")
    assert head.content_type == "text/plain"
    assert head.metadata == {"team": "core"}
    assert backend.get_object("data", "a/b.txt") == b"hello"


def test_get_size_cap(backend):
    backend.create_bucket("data")
    backend.put_object("data", "big", b"x" * 50)
    with pytest.raises(StorageError, match="cap"):
        backend.get_object("data", "big", max_bytes=10)


def test_missing_object_translates_to_not_found(backend):
    backend.create_bucket("data")
    with pytest.raises(NotFoundError):
        backend.head_object("data", "ghost")
    with pytest.raises(NotFoundError):
        backend.delete_object("data", "ghost")


def test_list_with_delimiter_and_pagination(backend):
    backend.create_bucket("data")
    for key in ["logs/a.log", "logs/b.log", "root.txt"]:
        backend.put_object("data", key, b"x")
    page = backend.list_objects("data", delimiter="/")
    assert [o.key for o in page.objects] == ["root.txt"]
    assert page.prefixes == ["logs/"]

    first = backend.list_objects("data", max_keys=2)
    assert first.truncated and first.next_token
    rest = backend.list_objects("data", continuation_token=first.next_token)
    assert [o.key for o in first.objects] + [o.key for o in rest.objects] == [
        "logs/a.log",
        "logs/b.log",
        "root.txt",
    ]


def test_copy_and_metadata_replace(backend):
    backend.create_bucket("data")
    backend.put_object("data", "src", b"x", metadata={"v": "1"})
    info = backend.copy_object("data", "src", "data", "dst")
    assert info.metadata == {"v": "1"}
    updated = backend.set_object_metadata("data", "dst", {"v": "2"})
    assert updated.metadata == {"v": "2"}
    assert backend.get_object_metadata("data", "dst") == {"v": "2"}


def test_presigned_url(backend):
    backend.create_bucket("data")
    url = backend.presigned_url("data", "k", method="PUT", expires_in=60)
    assert "op=put_object" in url and "expires=60" in url
    with pytest.raises(StorageError, match="GET or PUT"):
        backend.presigned_url("data", "k", method="DELETE")


def test_capabilities(backend):
    assert backend.capabilities()["presigned_urls"] is True
