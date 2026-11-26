"""The Linked Data client against a loopback app (SCION-CENTRALIZED-CAPABILITIES-R055)."""

from __future__ import annotations

import httpx
import pytest
from rdflib import Graph, Literal, URIRef

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.transports import LinkedDataClient, LinkedDataError

BASE = "http://127.0.0.1:9"
NAME = URIRef("http://example.test/name")
TURTLE = {"Content-Type": "text/turtle; charset=utf-8"}


def _options() -> HttpClientOptions:
    return HttpClientOptions(base_url=BASE, retry=RetryPolicy(max_attempts=1))


class _App:
    """Serves one Turtle resource; stores a PUT and applies a PATCH."""

    def __init__(self) -> None:
        self.graph = Graph()
        self.graph.parse(
            data="@prefix ex: <http://example.test/> . <> ex:name 'one' .",
            format="turtle",
            publicID=f"{BASE}/r",
        )
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.method == "GET":
            body = self.graph.serialize(format="turtle")
            return httpx.Response(200, headers=TURTLE, content=body)
        if request.method in {"PUT", "POST"}:
            self.graph = Graph().parse(
                data=request.content.decode(),
                format="turtle",
                publicID=str(request.url),
            )
            return httpx.Response(201, headers={"Location": f"{BASE}/new"})
        if request.method == "PATCH":
            self.graph.update(request.content.decode())
            return httpx.Response(204)
        return httpx.Response(405)


def _client(handler: httpx.BaseTransport) -> LinkedDataClient:
    return LinkedDataClient(_options(), transport=handler)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R055.1")
def test_each_verb_negotiates_media_types_and_resolves_relative_iris() -> None:
    app = _App()
    client = _client(httpx.MockTransport(app))
    graph = client.get(f"{BASE}/r")
    assert (URIRef(f"{BASE}/r"), NAME, Literal("one")) in graph
    accept = app.seen[0].headers["accept"]
    assert accept.startswith("text/turtle") and "application/rdf+xml" in accept
    assert client.put(f"{BASE}/r", graph) == f"{BASE}/new"
    assert app.seen[1].headers["content-type"] == "text/turtle"
    assert client.post(f"{BASE}/c", graph) == f"{BASE}/new"
    assert client.patch(f"{BASE}/r", "DELETE WHERE { ?s ?p ?o }") is None
    assert app.seen[3].headers["content-type"] == "application/sparql-update"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R055.2")
def test_each_category_and_no_server_text_in_message() -> None:
    planted = "planted-server-text"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/html":
            return httpx.Response(
                200, headers={"Content-Type": "text/html"}, content=planted
            )
        if path == "/bad":
            return httpx.Response(200, headers=TURTLE, content=planted)
        if path == "/write":
            return httpx.Response(409, content=planted)
        if path == "/read":
            return httpx.Response(404, content=planted)
        raise httpx.ConnectError(planted)

    client = _client(httpx.MockTransport(handler))
    graph = Graph()
    cases = [
        (lambda: client.get(f"{BASE}/html"), "NON_RDF_RESPONSE"),
        (lambda: client.get(f"{BASE}/bad"), "NON_RDF_RESPONSE"),
        (lambda: client.put(f"{BASE}/write", graph), "WRITE_REJECTED"),
        (lambda: client.get(f"{BASE}/read"), "READ_REJECTED"),
        (lambda: client.get(f"{BASE}/down"), "TRANSPORT_FAILED"),
    ]
    for call, category in cases:
        with pytest.raises(LinkedDataError) as caught:
            call()
        assert caught.value.category.value == category
        assert planted not in str(caught.value)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R055.3")
def test_client_is_a_public_transport() -> None:
    import scion_sdk.transports as transports

    assert {"LinkedDataClient", "LinkedDataError"} <= set(transports.__all__)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R055.4")
def test_loopback_get_put_post_patch_round_trip() -> None:
    app = _App()
    client = _client(httpx.MockTransport(app))
    url = f"{BASE}/r"
    graph = client.get(url)
    graph.add((URIRef(url), NAME, Literal("two")))
    client.put(url, graph)
    assert {str(o) for o in client.get(url).objects(URIRef(url), NAME)} == {
        "one",
        "two",
    }
    client.patch(url, f'DELETE DATA {{ <{url}> <{NAME}> "one" }}')
    assert {str(o) for o in client.get(url).objects(URIRef(url), NAME)} == {"two"}
