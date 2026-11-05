"""SCION-CENTRALIZED-CAPABILITIES-R042: the public storage surface holds together."""

import pytest

from scion_sdk.storage import base
from scion_sdk.storage.base import (
    STORAGE_BACKEND_FAILED,
    STORAGE_BUCKET_NOT_EMPTY,
    STORAGE_CODES,
    STORAGE_CONFIG_INVALID,
    STORAGE_DEPENDENCY_MISSING,
    STORAGE_EGRESS_REFUSED,
    STORAGE_EXISTS,
    STORAGE_NAME_INVALID,
    STORAGE_NOT_FOUND,
    STORAGE_SECRET_LITERAL,
    STORAGE_TOO_LARGE,
    STORAGE_UNSUPPORTED,
    BucketInfo,
    Metadata,
    MissingDependencyError,
    ObjectInfo,
    ObjectPage,
)
from scion_sdk.storage.config import BACKEND_ALIASES, CREDENTIAL_FIELDS, StoreConfig
from scion_sdk.storage.webdav_client import DEFAULT_MAX_XML_BYTES, DavClient, DavEntry
from tests.storage.dav_double import BASE_URL, DavDouble


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042")
def test_storage_surface_codes_models_and_registry():
    codes = {
        STORAGE_BACKEND_FAILED,
        STORAGE_BUCKET_NOT_EMPTY,
        STORAGE_CONFIG_INVALID,
        STORAGE_DEPENDENCY_MISSING,
        STORAGE_EGRESS_REFUSED,
        STORAGE_EXISTS,
        STORAGE_NAME_INVALID,
        STORAGE_NOT_FOUND,
        STORAGE_SECRET_LITERAL,
        STORAGE_TOO_LARGE,
        STORAGE_UNSUPPORTED,
    }
    assert codes == STORAGE_CODES
    assert MissingDependencyError("x").code == STORAGE_DEPENDENCY_MISSING
    meta: Metadata = {"a": "b"}
    info = ObjectInfo(key="k", size=1, metadata=dict(meta))
    page = ObjectPage(objects=[info])
    assert page.to_dict()["objects"][0]["key"] == "k"
    assert BucketInfo(name="b").to_dict()["name"] == "b"
    assert set(CREDENTIAL_FIELDS) == set(BACKEND_ALIASES.values())
    assert StoreConfig(name="s", backend="minio").kind == "s3"
    assert DEFAULT_MAX_XML_BYTES == 4 * 1024 * 1024
    dav = DavDouble()
    with DavClient(BASE_URL, transport=dav.transport) as client:
        client.make_collection("A")
        client.write_path("A/b/c.txt", b"x")
        client.move_path("A/b/c.txt", "A/d.txt")
        entries = client.propfind("A", 1)
    assert all(isinstance(e, DavEntry) for e in entries)
    assert {e.name for e in entries} >= {"d.txt", "b"}
    assert base.validate_key("a/b") == "a/b"
