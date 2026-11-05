"""SCION-CENTRALIZED-CAPABILITIES-R042.6: the store registry holds secret references only."""

import pytest

from scion_sdk.storage.base import SecretLiteralError, StoreConfigError
from scion_sdk.storage.config import StoreConfig, parse_stores
from scion_sdk.storage.factory import create_backend

pytest.importorskip("boto3")


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.6")
def test_literal_secret_refuses_and_aliases_resolve(monkeypatch, tmp_path):
    with pytest.raises(SecretLiteralError) as excinfo:
        parse_stores(
            {"m": {"backend": "s3", "credentials": {"access_key_id": "AKIA-literal"}}}
        )
    assert excinfo.value.code == "STORAGE_SECRET_LITERAL"
    with pytest.raises(StoreConfigError):
        parse_stores({"m": {"backend": "s3", "profile": "prod"}})
    with pytest.raises(StoreConfigError):
        parse_stores({"m": {"backend": "tape"}})
    with pytest.raises(StoreConfigError):
        create_backend(StoreConfig(name="m", backend="tape"))
    monkeypatch.setenv("TEST_KEY_ID", "id")
    monkeypatch.setenv("TEST_SECRET", "secret")
    stores = parse_stores(
        {
            "m": {
                "backend": "minio",
                "endpoint": "http://127.0.0.1:9000",
                "credentials": {
                    "access_key_id": "env://TEST_KEY_ID",
                    "secret_access_key": "env://TEST_SECRET",
                },
            },
            "f": {"backend": "fs", "root": str(tmp_path)},
        }
    )
    backend = create_backend(stores["m"])
    assert backend.backend_type == "s3"
    assert create_backend(stores["f"]).backend_type == "filesystem"
