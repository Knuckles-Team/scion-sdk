"""The reconciliation client against a loopback service (R054)."""

from __future__ import annotations

import json
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.transports import ReconciliationClient, ReconciliationError

ENDPOINT = "http://127.0.0.1:9/reconcile"
SPACE = "http://example.test/entity/"


def _client(handler: httpx.MockTransport) -> ReconciliationClient:
    options = HttpClientOptions(
        base_url="http://127.0.0.1:9", retry=RetryPolicy(max_attempts=1)
    )
    return ReconciliationClient(options, transport=handler)


def _service(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json={"identifierSpace": SPACE})
    form = parse_qs(request.content.decode())
    query = json.loads(form["queries"][0])["q0"]
    if query["query"] == "nobody":
        return httpx.Response(200, json={"q0": {"result": []}})
    if query["query"] == "html":
        return httpx.Response(200, content="<html>")
    if query["query"] == "denied":
        return httpx.Response(403, content="planted")
    results = [
        {"id": "Q2", "name": "Second", "score": 41.5, "match": False},
        {"id": "Q1", "name": "First", "description": "d", "score": 99, "match": True},
    ]
    return httpx.Response(200, json={"q0": {"result": results}})


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R054.1")
def test_candidates_resolve_ids_and_each_failure_category() -> None:
    client = _client(httpx.MockTransport(_service))
    found = client.reconcile(ENDPOINT, "someone", type="Person", limit=5)
    assert [c.entity for c in found] == [SPACE + "Q2", SPACE + "Q1"]
    assert found[1].score == Decimal("99") and found[1].match is True
    assert client.reconcile(ENDPOINT, "nobody") == []
    for name, category in (("html", "NON_JSON_RESPONSE"), ("denied", "READ_REJECTED")):
        with pytest.raises(ReconciliationError) as caught:
            client.reconcile(ENDPOINT, name)
        assert caught.value.category.value == category
        assert "planted" not in str(caught.value)
    bare = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    with pytest.raises(ReconciliationError) as caught:
        bare.reconcile(ENDPOINT, "x")
    assert caught.value.category.value == "MANIFEST_INVALID"
    with pytest.raises(ValueError, match="limit"):
        client.reconcile(ENDPOINT, "x", limit=0)
