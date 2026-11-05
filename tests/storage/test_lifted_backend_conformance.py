"""Protocol-conformance suite (CONCEPT:OB-OS.governance.every-provider-implements-same).

Every behavior the :class:`ObjectStoreBackend` protocol promises is exercised
here for real against the filesystem backend — no mocks. Cloud backends reuse
the same contract; adding a live backend to the ``backend`` fixture (e.g. a
MinIO integration profile) runs this whole suite against it unchanged.
"""

import pytest

from scion_sdk.storage.base import (
    AlreadyExistsError,
    BucketNotEmptyError,
    InvalidNameError,
    NotFoundError,
    ObjectStoreBackend,
    StorageError,
    UnsupportedOperationError,
)


@pytest.fixture(params=["filesystem"])
def backend(request, fs_backend):
    if request.param == "filesystem":
        return fs_backend
    raise ValueError(request.param)


def test_satisfies_protocol(backend):
    assert isinstance(backend, ObjectStoreBackend)
    caps = backend.capabilities()
    assert {"presigned_urls", "object_metadata", "bucket_location"} <= set(caps)


class TestBuckets:
    def test_lifecycle(self, backend):
        assert backend.list_buckets() == []
        assert not backend.bucket_exists("alpha")
        created = backend.create_bucket("alpha")
        assert created.name == "alpha"
        assert created.location is None
        assert backend.bucket_exists("alpha")
        assert [b.name for b in backend.list_buckets()] == ["alpha"]
        info = backend.bucket_info("alpha")
        assert info.name == "alpha"
        backend.delete_bucket("alpha")
        assert not backend.bucket_exists("alpha")

    def test_create_duplicate_raises(self, backend):
        backend.create_bucket("alpha")
        with pytest.raises(AlreadyExistsError):
            backend.create_bucket("alpha")

    def test_delete_missing_raises(self, backend):
        with pytest.raises(NotFoundError):
            backend.delete_bucket("ghost")

    def test_delete_non_empty_refused(self, backend):
        backend.create_bucket("alpha")
        backend.put_object("alpha", "keep.txt", b"data")
        with pytest.raises(BucketNotEmptyError):
            backend.delete_bucket("alpha")
        backend.delete_object("alpha", "keep.txt")
        backend.delete_bucket("alpha")

    def test_info_missing_raises(self, backend):
        with pytest.raises(NotFoundError):
            backend.bucket_info("ghost")

    def test_invalid_names_rejected(self, backend):
        for bad in ["", "a/b", "..", ".hidden"]:
            with pytest.raises(InvalidNameError):
                backend.create_bucket(bad)


class TestObjects:
    @pytest.fixture(autouse=True)
    def bucket(self, backend):
        backend.create_bucket("data")
        return "data"

    def test_put_get_head_roundtrip(self, backend):
        info = backend.put_object(
            "data",
            "docs/report.txt",
            b"hello world",
            content_type="text/plain",
            metadata={"owner": "ops"},
        )
        assert info.key == "docs/report.txt"
        assert info.size == 11
        assert backend.get_object("data", "docs/report.txt") == b"hello world"
        head = backend.head_object("data", "docs/report.txt")
        assert head.size == 11
        assert head.content_type == "text/plain"
        assert head.metadata == {"owner": "ops"}
        assert head.etag
        assert head.last_modified

    def test_put_overwrites_and_etag_changes(self, backend):
        first = backend.put_object("data", "k", b"one")
        second = backend.put_object("data", "k", b"two")
        assert first.etag != second.etag
        assert backend.get_object("data", "k") == b"two"

    def test_get_missing_raises(self, backend):
        with pytest.raises(NotFoundError):
            backend.get_object("data", "ghost")
        with pytest.raises(NotFoundError):
            backend.head_object("data", "ghost")

    def test_get_into_missing_bucket_raises(self, backend):
        with pytest.raises(NotFoundError):
            backend.get_object("ghost-bucket", "k")

    def test_get_respects_size_cap(self, backend):
        backend.put_object("data", "big.bin", b"x" * 100)
        with pytest.raises(StorageError, match="cap"):
            backend.get_object("data", "big.bin", max_bytes=10)
        assert len(backend.get_object("data", "big.bin", max_bytes=100)) == 100

    def test_key_traversal_rejected(self, backend):
        for bad in ["../escape", "a/../../b", "/abs", "a//b", ""]:
            with pytest.raises(InvalidNameError):
                backend.put_object("data", bad, b"x")

    def test_symlink_directory_escape_rejected(self, backend, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        link = backend.root / "data" / "redirect"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:  # pragma: no cover - platform policy
            pytest.skip(f"symlinks unavailable: {exc}")

        with pytest.raises(InvalidNameError, match="symbolic links"):
            backend.put_object("data", "redirect/escape.txt", b"blocked")
        assert not (outside / "escape.txt").exists()

    def test_symlink_payload_read_rejected(self, backend, tmp_path):
        outside = tmp_path / "sensitive.txt"
        outside.write_bytes(b"must-not-cross-store-boundary")
        link = backend.root / "data" / "alias.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:  # pragma: no cover - platform policy
            pytest.skip(f"symlinks unavailable: {exc}")

        with pytest.raises(InvalidNameError, match="symbolic links"):
            backend.get_object("data", "alias.txt")

    def test_copy(self, backend):
        backend.create_bucket("backup")
        backend.put_object(
            "data",
            "src.txt",
            b"payload",
            content_type="text/plain",
            metadata={"tag": "v1"},
        )
        info = backend.copy_object("data", "src.txt", "backup", "dst.txt")
        assert info.key == "dst.txt"
        assert backend.get_object("backup", "dst.txt") == b"payload"
        assert backend.get_object_metadata("backup", "dst.txt") == {"tag": "v1"}
        # Source untouched.
        assert backend.get_object("data", "src.txt") == b"payload"

    def test_copy_missing_source_raises(self, backend):
        with pytest.raises(NotFoundError):
            backend.copy_object("data", "ghost", "data", "dst")

    def test_delete(self, backend):
        backend.put_object("data", "tmp/x.txt", b"x")
        backend.delete_object("data", "tmp/x.txt")
        with pytest.raises(NotFoundError):
            backend.head_object("data", "tmp/x.txt")
        with pytest.raises(NotFoundError):
            backend.delete_object("data", "tmp/x.txt")

    def test_metadata_get_set(self, backend):
        backend.put_object("data", "k", b"x")
        assert backend.get_object_metadata("data", "k") == {}
        info = backend.set_object_metadata("data", "k", {"env": "prod"})
        assert info.metadata == {"env": "prod"}
        assert backend.get_object_metadata("data", "k") == {"env": "prod"}
        backend.set_object_metadata("data", "k", {})
        assert backend.get_object_metadata("data", "k") == {}

    def test_presign_capability_consistent(self, backend):
        backend.put_object("data", "k", b"x")
        if backend.capabilities()["presigned_urls"]:
            url = backend.presigned_url("data", "k")
            assert isinstance(url, str) and url
        else:
            with pytest.raises(UnsupportedOperationError):
                backend.presigned_url("data", "k")


class TestListing:
    @pytest.fixture(autouse=True)
    def populated(self, backend):
        backend.create_bucket("data")
        for key in [
            "logs/2026/01.log",
            "logs/2026/02.log",
            "logs/old/00.log",
            "readme.md",
            "reports/q1.pdf",
        ]:
            backend.put_object("data", key, b"content")

    def test_list_all(self, backend):
        page = backend.list_objects("data")
        assert [o.key for o in page.objects] == [
            "logs/2026/01.log",
            "logs/2026/02.log",
            "logs/old/00.log",
            "readme.md",
            "reports/q1.pdf",
        ]
        assert not page.truncated
        assert page.next_token is None

    def test_list_prefix(self, backend):
        page = backend.list_objects("data", prefix="logs/2026/")
        assert [o.key for o in page.objects] == [
            "logs/2026/01.log",
            "logs/2026/02.log",
        ]

    def test_list_delimiter_folds_prefixes(self, backend):
        page = backend.list_objects("data", delimiter="/")
        assert [o.key for o in page.objects] == ["readme.md"]
        assert page.prefixes == ["logs/", "reports/"]

    def test_list_prefix_and_delimiter(self, backend):
        page = backend.list_objects("data", prefix="logs/", delimiter="/")
        assert page.objects == []
        assert page.prefixes == ["logs/2026/", "logs/old/"]

    def test_pagination_walks_everything(self, backend):
        collected: list = []
        token = None
        pages = 0
        while True:
            page = backend.list_objects("data", max_keys=2, continuation_token=token)
            collected.extend(o.key for o in page.objects)
            pages += 1
            if not page.truncated:
                break
            token = page.next_token
            assert token is not None
        assert pages >= 3
        assert collected == [
            "logs/2026/01.log",
            "logs/2026/02.log",
            "logs/old/00.log",
            "readme.md",
            "reports/q1.pdf",
        ]

    def test_list_missing_bucket_raises(self, backend):
        with pytest.raises(NotFoundError):
            backend.list_objects("ghost")
