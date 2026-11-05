"""SCION-CENTRALIZED-CAPABILITIES-R042.2: the S3-compatible backend against an in-process S3 double."""

from urllib.parse import urlsplit

import pytest

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

from scion_sdk.storage.base import NotFoundError, StoreConfigError  # noqa: E402
from scion_sdk.storage.s3 import S3Backend, S3Credentials  # noqa: E402


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.2")
def test_s3_round_trip_and_presign_uses_public_endpoint():
    with moto.mock_aws():
        backend = S3Backend(
            credentials=S3Credentials("testing", "testing"),
            presign_endpoint="https://objects.example",
        )
        backend.create_bucket("data")
        backend.put_object("data", "a/k.txt", b"hello", content_type="text/plain")
        assert backend.get_object("data", "a/k.txt") == b"hello"
        backend.copy_object("data", "a/k.txt", "data", "b/k.txt")
        assert [o.key for o in backend.list_objects("data").objects] == [
            "a/k.txt",
            "b/k.txt",
        ]
        url = backend.presigned_url("data", "a/k.txt", expires_in=60)
        assert urlsplit(url).hostname == "objects.example"
        backend.delete_object("data", "a/k.txt")
        with pytest.raises(NotFoundError):
            backend.head_object("data", "a/k.txt")
    assert repr(S3Credentials("id", "secret")) == "S3Credentials(<redacted>)"
    with pytest.raises(StoreConfigError):
        S3Backend(endpoint="https://objects.example")
