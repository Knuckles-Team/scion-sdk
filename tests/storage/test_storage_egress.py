"""SCION-CENTRALIZED-CAPABILITIES-R042.7: storage endpoints pass the egress guard."""

import pytest

from scion_sdk.storage.base import EgressRefusedError
from scion_sdk.storage.config import parse_store
from scion_sdk.storage.factory import create_backend, endpoint_decision


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.7")
def test_private_endpoint_refuses_unless_allowed(monkeypatch):
    monkeypatch.setenv("DAV_USER", "svc")
    monkeypatch.setenv("DAV_PASS", "pw")
    with pytest.raises(EgressRefusedError) as excinfo:
        endpoint_decision("https://10.1.2.3/dav")
    assert excinfo.value.code == "STORAGE_EGRESS_REFUSED"
    with pytest.raises(EgressRefusedError):
        endpoint_decision("http://files.example/dav")
    userinfo = (
        "https://user:pw@files.example/dav"  # sanitizer:ignore - synthetic, refused
    )
    with pytest.raises(EgressRefusedError):
        endpoint_decision(userinfo)
    endpoint_decision("http://10.1.2.3/dav", ("10.1.2.3",))
    store = parse_store(
        "files",
        {
            "backend": "webdav",
            "endpoint": "https://10.1.2.3/dav",
            "allowed_private_hosts": ["10.1.2.3"],
            "credentials": {"username": "env://DAV_USER", "password": "env://DAV_PASS"},
        },
    )
    assert create_backend(store).backend_type == "webdav"
    blocked = parse_store(
        "files", {"backend": "webdav", "endpoint": "https://10.1.2.3/dav"}
    )
    with pytest.raises(EgressRefusedError):
        create_backend(blocked)
