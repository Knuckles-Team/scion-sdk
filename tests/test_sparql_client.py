"""The SPARQL client against a loopback endpoint (SCION-CENTRALIZED-CAPABILITIES-R052)."""

from __future__ import annotations

import json

import httpx
import pytest

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.transports import SparqlClient, SparqlError

ENDPOINT = "http://127.0.0.1:9/sparql"
TURTLE = {"Content-Type": "text/turtle"}
SELECT = {
    "head": {"vars": ["s", "label"]},
    "results": {
        "bindings": [
            {
                "s": {"type": "uri", "value": "http://example.test/a"},
                "label": {"type": "literal", "value": "A", "xml:lang": "en"},
            },
            {
                "s": {"type": "uri", "value": "http://example.test/b"},
                "label": {
                    "type": "literal",
                    "value": "7",
                    "datatype": "http://www.w3.org/2001/XMLSchema#integer",
                },
            },
        ]
    },
}


def _client(handler: httpx.MockTransport, **kwargs: int) -> SparqlClient:
    options = HttpClientOptions(
        base_url="http://127.0.0.1:9", retry=RetryPolicy(max_attempts=1)
    )
    return SparqlClient(ENDPOINT, options, transport=handler, **kwargs)


def _endpoint(request: httpx.Request) -> httpx.Response:
    text = request.content.decode()
    kind = request.headers["content-type"]
    if kind == "application/sparql-update":
        return httpx.Response(204 if "INSERT" in text else 400)
    if "SELECT" in text:
        return httpx.Response(200, json=SELECT)
    if "ASK" in text:
        return httpx.Response(200, json={"head": {}, "boolean": True})
    if "refuse" in text:
        return httpx.Response(400, content="planted")
    if "huge" in text:
        return httpx.Response(200, content=b"x" * 70_000)
    return httpx.Response(
        200,
        headers=TURTLE,
        content="<http://example.test/a> <http://example.test/p> 1 .",
    )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R052.1")
def test_select_ask_construct_describe_update_and_each_failure_category() -> None:
    client = _client(httpx.MockTransport(_endpoint), max_result_bytes=65_536)
    result = client.select("SELECT ?s ?label WHERE { ?s ?p ?label }")
    assert result.variables == ("s", "label")
    assert result.rows[0]["label"] == {
        "type": "literal",
        "value": "A",
        "language": "en",
    }
    assert result.rows[1]["label"]["datatype"].endswith("#integer")
    assert client.ask("ASK { ?s ?p ?o }") is True
    assert len(client.construct("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")) == 1
    assert len(client.describe("DESCRIBE <http://example.test/a>")) == 1
    client.update("INSERT DATA { <http://example.test/a> <http://example.test/p> 2 }")
    for call, category in (
        (lambda: client.select("refuse"), "QUERY_REFUSED"),
        (lambda: client.select("huge"), "RESULT_TOO_LARGE"),
        (lambda: client.update("bad"), "WRITE_REJECTED"),
    ):
        with pytest.raises(SparqlError) as caught:
            call()
        assert caught.value.category.value == category
        assert "planted" not in str(caught.value)
    with pytest.raises(ValueError, match="max_result_bytes"):
        _client(httpx.MockTransport(_endpoint), max_result_bytes=10)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R052.2")
def test_sparql_client_is_a_public_transport() -> None:
    import scion_sdk.transports as transports

    assert {"SparqlClient", "SparqlError"} <= set(transports.__all__)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R052.3")
def test_loopback_query_forms_and_update() -> None:
    seen: list[str] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["content-type"])
        return _endpoint(request)

    client = _client(httpx.MockTransport(endpoint))
    assert json.dumps(client.select("SELECT * WHERE {?s ?p ?o}").variables)
    assert client.ask("ASK {}")
    assert client.construct("CONSTRUCT {} WHERE {}") is not None
    assert client.describe("DESCRIBE <http://example.test/a>") is not None
    client.update("INSERT DATA {}")
    assert seen == ["application/sparql-query"] * 4 + ["application/sparql-update"]
