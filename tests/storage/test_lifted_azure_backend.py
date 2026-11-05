"""Azure Blob backend tests with a mocked BlobServiceClient."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scion_sdk.storage.azure_blob import AzureBlobBackend
from scion_sdk.storage.base import (
    AlreadyExistsError,
    BucketNotEmptyError,
    NotFoundError,
    StorageError,
)

NOW = datetime(2026, 6, 11, tzinfo=UTC)


class FakeNotFound(Exception):
    status_code = 404


class FakeConflict(Exception):
    status_code = 409


def make_blob_props(name="k", size=5, metadata=None, content_type="text/plain"):
    return SimpleNamespace(
        name=name,
        size=size,
        etag='"etag-1"',
        last_modified=NOW,
        content_settings=SimpleNamespace(content_type=content_type),
        metadata=metadata or {},
        blob_tier="Hot",
    )


@pytest.fixture
def client():
    return MagicMock()


@pytest.fixture
def backend(client):
    return AzureBlobBackend(client=client)


def test_list_buckets(backend, client):
    client.list_containers.return_value = [
        SimpleNamespace(name="alpha", last_modified=NOW)
    ]
    buckets = backend.list_buckets()
    assert buckets[0].name == "alpha"
    assert buckets[0].created == NOW.isoformat()


def test_create_bucket_conflict(backend, client):
    client.create_container.side_effect = FakeConflict("exists")
    with pytest.raises(AlreadyExistsError):
        backend.create_bucket("alpha")


def test_delete_bucket_requires_empty(backend, client):
    container = client.get_container_client.return_value
    container.list_blobs.return_value = iter([make_blob_props()])
    with pytest.raises(BucketNotEmptyError):
        backend.delete_bucket("alpha")
    container.list_blobs.return_value = iter([])
    backend.delete_bucket("alpha")
    container.delete_container.assert_called_once()


def test_bucket_exists_and_info(backend, client):
    container = client.get_container_client.return_value
    container.exists.return_value = True
    assert backend.bucket_exists("alpha")
    container.get_container_properties.side_effect = FakeNotFound("missing")
    with pytest.raises(NotFoundError):
        backend.bucket_info("ghost")


def test_list_objects_with_prefix_markers(backend, client):
    class BlobPrefix:
        def __init__(self, name):
            self.name = name

    container = client.get_container_client.return_value
    iterator = container.walk_blobs.return_value
    iterator.by_page.return_value = iter(
        [[make_blob_props("root.txt"), BlobPrefix("logs/")]]
    )
    page = backend.list_objects("data", delimiter="/")
    assert [o.key for o in page.objects] == ["root.txt"]
    assert page.prefixes == ["logs/"]
    assert not page.truncated


def test_head_object(backend, client):
    blob_client = client.get_blob_client.return_value
    blob_client.get_blob_properties.return_value = make_blob_props(
        metadata={"team": "core"}
    )
    info = backend.head_object("data", "k")
    assert info.key == "k"
    assert info.size == 5
    assert info.etag == "etag-1"
    assert info.metadata == {"team": "core"}


def test_head_missing_translates(backend, client):
    blob_client = client.get_blob_client.return_value
    blob_client.get_blob_properties.side_effect = FakeNotFound("missing")
    with pytest.raises(NotFoundError):
        backend.head_object("data", "ghost")


def test_get_object_cap_and_read(backend, client):
    blob_client = client.get_blob_client.return_value
    blob_client.get_blob_properties.return_value = make_blob_props(size=100)
    with pytest.raises(StorageError, match="cap"):
        backend.get_object("data", "k", max_bytes=10)
    blob_client.download_blob.return_value.readall.return_value = b"x" * 100
    assert backend.get_object("data", "k") == b"x" * 100


def test_put_object(backend, client):
    blob_client = client.get_blob_client.return_value
    info = backend.put_object("data", "k", b"hello", metadata={"a": "1"})
    blob_client.upload_blob.assert_called_once()
    args, kwargs = blob_client.upload_blob.call_args
    assert args[0] == b"hello"
    assert kwargs["overwrite"] is True
    assert kwargs["metadata"] == {"a": "1"}
    assert info.size == 5


def test_copy_object(backend, client):
    blob_client = client.get_blob_client.return_value
    blob_client.get_blob_properties.return_value = make_blob_props("dst")
    info = backend.copy_object("data", "src", "backup", "dst")
    blob_client.start_copy_from_url.assert_called_once()
    assert info.key == "dst"


def test_delete_object(backend, client):
    blob_client = client.get_blob_client.return_value
    backend.delete_object("data", "k")
    blob_client.delete_blob.assert_called_once()
    blob_client.delete_blob.side_effect = FakeNotFound("missing")
    with pytest.raises(NotFoundError):
        backend.delete_object("data", "ghost")


def test_set_metadata(backend, client):
    blob_client = client.get_blob_client.return_value
    blob_client.get_blob_properties.return_value = make_blob_props(
        metadata={"env": "prod"}
    )
    info = backend.set_object_metadata("data", "k", {"env": "prod"})
    blob_client.set_blob_metadata.assert_called_once_with({"env": "prod"})
    assert info.metadata == {"env": "prod"}


def test_presign_requires_account_key(backend, client):
    client.credential = SimpleNamespace(account_key=None)
    with pytest.raises(StorageError):
        backend.presigned_url("data", "k")
    with pytest.raises(StorageError, match="GET or PUT"):
        backend.presigned_url("data", "k", method="DELETE")
