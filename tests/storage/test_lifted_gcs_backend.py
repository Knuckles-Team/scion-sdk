"""GCS backend tests with a mocked google-cloud-storage client."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scion_sdk.storage.base import (
    AlreadyExistsError,
    BucketNotEmptyError,
    NotFoundError,
    StorageError,
)
from scion_sdk.storage.gcs import GCSBackend

NOW = datetime(2026, 6, 11, tzinfo=UTC)


class FakeNotFound(Exception):
    code = 404


class FakeConflict(Exception):
    code = 409


def make_blob(name="k", size=5, metadata=None, content_type="text/plain"):
    blob = MagicMock()
    blob.name = name
    blob.size = size
    blob.etag = "etag-1"
    blob.updated = NOW
    blob.content_type = content_type
    blob.metadata = metadata or {}
    blob.storage_class = "STANDARD"
    return blob


@pytest.fixture
def client():
    return MagicMock()


@pytest.fixture
def backend(client):
    return GCSBackend(client=client)


def test_list_buckets(backend, client):
    client.list_buckets.return_value = [
        SimpleNamespace(name="alpha", time_created=NOW, location="US")
    ]
    buckets = backend.list_buckets()
    assert buckets[0].name == "alpha"
    assert buckets[0].location == "US"
    assert buckets[0].created == NOW.isoformat()


def test_create_bucket_conflict(backend, client):
    client.create_bucket.side_effect = FakeConflict("exists")
    with pytest.raises(AlreadyExistsError):
        backend.create_bucket("alpha")


def test_delete_non_empty_bucket(backend, client):
    handle = client.bucket.return_value
    handle.delete.side_effect = FakeConflict("not empty")
    with pytest.raises(BucketNotEmptyError):
        backend.delete_bucket("alpha")


def test_bucket_exists(backend, client):
    client.lookup_bucket.return_value = SimpleNamespace(name="alpha")
    assert backend.bucket_exists("alpha")
    client.lookup_bucket.return_value = None
    assert not backend.bucket_exists("ghost")


def test_bucket_info_not_found(backend, client):
    client.get_bucket.side_effect = FakeNotFound("missing")
    with pytest.raises(NotFoundError):
        backend.bucket_info("ghost")


def test_list_objects(backend, client):
    iterator = MagicMock()
    iterator.__iter__ = lambda self: iter([make_blob("logs/a.log")])
    iterator.next_page_token = "tok"
    iterator.prefixes = {"logs/"}
    client.list_blobs.return_value = iterator
    page = backend.list_objects("data", prefix="logs/", delimiter="/", max_keys=10)
    assert [o.key for o in page.objects] == ["logs/a.log"]
    assert page.prefixes == ["logs/"]
    assert page.truncated and page.next_token == "tok"
    client.list_blobs.assert_called_once_with(
        "data", prefix="logs/", delimiter="/", max_results=10, page_token=None
    )


def test_head_and_metadata(backend, client):
    blob = make_blob(metadata={"team": "core"})
    client.bucket.return_value.get_blob.return_value = blob
    info = backend.head_object("data", "k")
    assert info.size == 5
    assert info.metadata == {"team": "core"}
    assert backend.get_object_metadata("data", "k") == {"team": "core"}


def test_head_missing_blob(backend, client):
    client.bucket.return_value.get_blob.return_value = None
    with pytest.raises(NotFoundError):
        backend.head_object("data", "ghost")


def test_get_object_respects_cap(backend, client):
    blob = make_blob(size=100)
    client.bucket.return_value.get_blob.return_value = blob
    with pytest.raises(StorageError, match="cap"):
        backend.get_object("data", "k", max_bytes=10)
    blob.download_as_bytes.return_value = b"x" * 100
    assert backend.get_object("data", "k") == b"x" * 100


def test_put_object(backend, client):
    blob = client.bucket.return_value.blob.return_value
    info = backend.put_object(
        "data", "k", b"hello", content_type="text/plain", metadata={"a": "1"}
    )
    blob.upload_from_string.assert_called_once_with(b"hello", content_type="text/plain")
    assert blob.metadata == {"a": "1"}
    assert info.size == 5


def test_copy_object(backend, client):
    source = make_blob("src")
    client.bucket.return_value.get_blob.return_value = source
    client.bucket.return_value.copy_blob.return_value = make_blob("dst")
    info = backend.copy_object("data", "src", "backup", "dst")
    assert info.key == "dst"


def test_delete_object(backend, client):
    blob = make_blob()
    client.bucket.return_value.get_blob.return_value = blob
    backend.delete_object("data", "k")
    blob.delete.assert_called_once()


def test_set_metadata_patches(backend, client):
    blob = make_blob()
    client.bucket.return_value.get_blob.return_value = blob
    backend.set_object_metadata("data", "k", {"env": "prod"})
    assert blob.metadata == {"env": "prod"}
    blob.patch.assert_called_once()


def test_presign(backend, client):
    blob = client.bucket.return_value.blob.return_value
    blob.generate_signed_url.return_value = "https://signed"
    assert backend.presigned_url("data", "k", method="GET") == "https://signed"
    with pytest.raises(StorageError, match="GET or PUT"):
        backend.presigned_url("data", "k", method="DELETE")


def test_presign_without_signing_creds(backend, client):
    blob = client.bucket.return_value.blob.return_value
    blob.generate_signed_url.side_effect = AttributeError("no private key")
    with pytest.raises(StorageError, match="service-account"):
        backend.presigned_url("data", "k")
