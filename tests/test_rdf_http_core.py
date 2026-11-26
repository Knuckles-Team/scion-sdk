"""The shared request core and result types of the RDF transport clients."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from scion_sdk.certify.evidence import EvidenceSubmitter
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.transports import Candidate, SelectResult
from scion_sdk.transports._rdf_http import Category, RdfHttp, RdfHttpError, Reply
from scion_sdk.transports.linked_data import ACCEPT


def test_core_returns_a_reply_and_categorises_failures_without_server_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(200, headers={"Location": "/n"}, content=b"body")
        return httpx.Response(500, content=b"planted")

    options = HttpClientOptions(
        base_url="http://127.0.0.1:9", retry=RetryPolicy(max_attempts=1)
    )
    http = RdfHttp(options, transport=httpx.MockTransport(handler))
    reply = http.send(
        "GET", "http://127.0.0.1:9/ok", write=False, max_bytes=100, headers={}
    )
    assert reply == Reply(200, "", "/n", b"body")
    with pytest.raises(RdfHttpError) as caught:
        http.send("PUT", "http://127.0.0.1:9/x", write=True, max_bytes=100)
    assert caught.value.category is Category.WRITE_REJECTED
    assert "planted" not in str(caught.value)
    assert "5xx" in str(caught.value)


def test_result_types_and_accept_header() -> None:
    assert SelectResult(("s",), ()).rows == ()
    candidate = Candidate("http://e/1", "n", "", Decimal(1), True)
    assert candidate.match
    assert "application/rdf+xml" in ACCEPT
    assert EvidenceSubmitter is not None
