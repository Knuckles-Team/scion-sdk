"""SCION-CENTRALIZED-CAPABILITIES-R042.5: the WebDAV backend over a WebDAV double."""

import pytest

from scion_sdk.storage.base import (
    StorageError,
    TooLargeError,
    UnsupportedOperationError,
)
from scion_sdk.storage.webdav import WebDavBackend
from tests.storage.dav_double import BASE_URL, DavDouble


def _backend(dav: DavDouble) -> WebDavBackend:
    return WebDavBackend(
        BASE_URL, username="svc", password="pw", transport=dav.transport
    )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.5")
def test_webdav_round_trip_creates_parents_and_refuses_doctype():
    dav = DavDouble()
    backend = _backend(dav)
    backend.put_object("Exports", "app/2026-11-04/run1/out.png", b"png", "image/png")
    methods = [m for m, _ in dav.requests]
    assert methods.index("MKCOL") < methods.index("PUT")
    assert dav.files["Exports/app/2026-11-04/run1/out.png"] == b"png"
    assert backend.get_object("Exports", "app/2026-11-04/run1/out.png") == b"png"
    assert backend.head_object("Exports", "app/2026-11-04/run1/out.png").size == 3
    keys = [o.key for o in backend.list_objects("Exports").objects]
    assert keys == ["app/2026-11-04/run1/out.png"]
    entries = backend.propfind(
        "Exports", 1, extra_props=(("http://owncloud.org/ns", "fileid"),)
    )
    assert entries[0].props["fileid"] == "42"
    with pytest.raises(TooLargeError):
        backend.get_object("Exports", "app/2026-11-04/run1/out.png", max_bytes=1)
    with pytest.raises(UnsupportedOperationError):
        backend.presigned_url("Exports", "app/2026-11-04/run1/out.png")
    backend.copy_object(
        "Exports", "app/2026-11-04/run1/out.png", "Exports", "copy/out.png"
    )
    backend.delete_object("Exports", "copy/out.png")
    assert "Exports/copy/out.png" not in dav.files

    hostile = DavDouble(doctype=True)
    with pytest.raises(StorageError):
        _backend(hostile).list_buckets()
