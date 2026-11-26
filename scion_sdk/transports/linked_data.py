"""Linked Data resource client: four verbs over RDF media types.

SCION-CENTRALIZED-CAPABILITIES-R055. Connectors read and write RDF resources
through :class:`LinkedDataClient`, never through their own HTTP code. Requests
go through ``build_http_client`` so the egress guard, retry policy and TLS
profile apply. Failures raise :class:`LinkedDataError` with a closed category
and never carry server text.
"""

from __future__ import annotations

import httpx
from rdflib import Graph

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.transports._rdf_http import Category, RdfHttp, RdfHttpError, Reply

__all__ = ["ACCEPT", "LinkedDataClient", "LinkedDataError", "parse_graph"]

ACCEPT = "text/turtle, application/n-triples, application/ld+json, application/rdf+xml"
_FORMATS = {
    "text/turtle": "turtle",
    "application/n-triples": "nt",
    "application/ld+json": "json-ld",
    "application/rdf+xml": "xml",
}
DEFAULT_MAX_BYTES = 67_108_864


class LinkedDataError(RdfHttpError):
    """A Linked Data exchange failed; ``category`` is closed."""


def parse_graph(content_type: str, body: bytes, base: str) -> Graph:
    """Parse an RDF answer by its ``Content-Type``, resolving against ``base``."""
    media = content_type.split(";", 1)[0].strip().lower()
    fmt = _FORMATS.get(media)
    if fmt is None:
        raise LinkedDataError(Category.NON_RDF_RESPONSE)
    graph = Graph()
    try:
        graph.parse(data=body, format=fmt, publicID=base)
    except Exception:
        raise LinkedDataError(Category.NON_RDF_RESPONSE) from None
    return graph


class LinkedDataClient:
    """GET, PUT, POST and PATCH of RDF resources."""

    def __init__(
        self,
        options: HttpClientOptions,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = RdfHttp(options, transport=transport)
        self._max_bytes = max_bytes

    def _send(
        self,
        method: str,
        url: str,
        *,
        write: bool,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> Reply:
        try:
            return self._http.send(
                method,
                url,
                write=write,
                max_bytes=self._max_bytes,
                headers=headers,
                content=content,
            )
        except RdfHttpError as error:
            raise LinkedDataError(error.category, error.status_class) from None

    def get(self, url: str) -> Graph:
        """Read a resource; the answer is parsed by its ``Content-Type``."""
        reply = self._send("GET", url, write=False, headers={"Accept": ACCEPT})
        return parse_graph(reply.content_type, reply.body, url)

    def _write(self, method: str, url: str, graph: Graph) -> str | None:
        body = graph.serialize(format="turtle", base=url).encode("utf-8")
        reply = self._send(
            method,
            url,
            write=True,
            headers={"Content-Type": "text/turtle"},
            content=body,
        )
        return reply.location or None

    def put(self, url: str, graph: Graph) -> str | None:
        """Replace a resource; returns the ``Location`` header when present."""
        return self._write("PUT", url, graph)

    def post(self, url: str, graph: Graph) -> str | None:
        """Create a resource in a container; returns ``Location`` when present."""
        return self._write("POST", url, graph)

    def patch(self, url: str, update: str) -> str | None:
        """Apply a SPARQL Update to a resource."""
        reply = self._send(
            "PATCH",
            url,
            write=True,
            headers={"Content-Type": "application/sparql-update"},
            content=update.encode("utf-8"),
        )
        return reply.location or None
