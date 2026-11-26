"""A connector call verifies against the enterprise root bundle (R017.1)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from scion_sdk.http.client import build_http_client
from scion_sdk.http.errors import TlsVerificationError
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.testing.certificates import issue_test_certificates
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse
from scion_sdk.tls.errors import TransportSecurityError
from scion_sdk.tls.resolve import resolve_tls_profile


def _client(base_url: str, bundle: Path) -> httpx.Client:
    profile = resolve_tls_profile("scion", settings={"SCION_CA_BUNDLE": str(bundle)})
    options = HttpClientOptions(
        base_url=base_url, tls=profile, retry=RetryPolicy(max_attempts=1)
    )
    return build_http_client(options)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R017.1")
def test_connector_call_verifies_against_bundle_and_refuses_mismatch(
    tmp_path: Path,
) -> None:
    served = issue_test_certificates()
    other = issue_test_certificates()
    good, bad, empty = (tmp_path / n for n in ("good.pem", "bad.pem", "empty.pem"))
    good.write_text(served.ca_pem)
    bad.write_text(other.ca_pem)
    empty.write_text("")
    context = served.server_context(tmp_path)
    ok = ScriptedResponse(body=b"{}", headers={"content-type": "application/json"})
    with ScriptedHttpServer(ok, ssl_context=context) as server:
        assert _client(server.base_url, good).get("/x").status_code == 200
        with pytest.raises(TlsVerificationError):
            _client(server.base_url, bad).get("/x")
    for unusable in (tmp_path / "missing.pem", empty):
        with pytest.raises(TransportSecurityError, match="tls_bundle_unavailable"):
            resolve_tls_profile("scion", settings={"SCION_CA_BUNDLE": str(unusable)})
