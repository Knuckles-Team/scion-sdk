"""SPARQL endpoint client: query forms and update over the governed HTTP client.

SCION-CENTRALIZED-CAPABILITIES-R052. ``select`` and ``ask`` return rows and a
boolean; ``construct`` and ``describe`` return a graph. An answer over
``max_result_bytes`` raises ``RESULT_TOO_LARGE`` without being parsed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from rdflib import Graph

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.transports._rdf_http import Category, RdfHttp, RdfHttpError, Reply
from scion_sdk.transports.linked_data import ACCEPT, parse_graph

__all__ = ["SelectResult", "SparqlClient", "SparqlError"]

DEFAULT_MAX_RESULT_BYTES = 67_108_864
_MIN_RESULT_BYTES = 65_536
_MAX_RESULT_BYTES = 1_073_741_824
_RESULTS = "application/sparql-results+json"


class SparqlError(RdfHttpError):
    """A SPARQL exchange failed; ``category`` is closed."""


@dataclass(frozen=True)
class SelectResult:
    """Variables and rows; each cell holds ``type``, ``value`` and when present
    ``datatype`` and ``language``."""

    variables: tuple[str, ...]
    rows: tuple[dict[str, dict[str, str]], ...]


class SparqlClient:
    """One SPARQL endpoint."""

    def __init__(
        self,
        endpoint: str,
        options: HttpClientOptions,
        *,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not _MIN_RESULT_BYTES <= max_result_bytes <= _MAX_RESULT_BYTES:
            raise ValueError("max_result_bytes is outside 65536 to 1073741824")
        self._endpoint = endpoint
        self._http = RdfHttp(options, transport=transport)
        self._max = max_result_bytes

    def _send(self, *, write: bool, headers: dict[str, str], body: str) -> Reply:
        try:
            return self._http.send(
                "POST",
                self._endpoint,
                write=write,
                max_bytes=self._max,
                headers=headers,
                content=body.encode("utf-8"),
                too_large=Category.RESULT_TOO_LARGE,
                refuse_400=True,
            )
        except RdfHttpError as error:
            raise SparqlError(error.category, error.status_class) from None

    def _query(self, query: str, accept: str) -> Reply:
        headers = {"Content-Type": "application/sparql-query", "Accept": accept}
        return self._send(write=False, headers=headers, body=query)

    def _json(self, query: str) -> dict[str, object]:
        reply = self._query(query, _RESULTS)
        try:
            document = json.loads(reply.body)
        except ValueError:
            raise SparqlError(Category.NON_JSON_RESPONSE) from None
        if not isinstance(document, dict):
            raise SparqlError(Category.NON_JSON_RESPONSE)
        return document

    def select(self, query: str) -> SelectResult:
        """Run a ``SELECT``; return its variables and rows."""
        document = self._json(query)
        head, results = document.get("head"), document.get("results")
        if not isinstance(head, dict) or not isinstance(results, dict):
            raise SparqlError(Category.NON_JSON_RESPONSE)
        variables = tuple(str(v) for v in head.get("vars", ()))
        bindings = results.get("bindings", ())
        if not isinstance(bindings, list):
            raise SparqlError(Category.NON_JSON_RESPONSE)
        rows = tuple(_row(binding) for binding in bindings)
        return SelectResult(variables, rows)

    def ask(self, query: str) -> bool:
        """Run an ``ASK``."""
        answer = self._json(query).get("boolean")
        if not isinstance(answer, bool):
            raise SparqlError(Category.NON_JSON_RESPONSE)
        return answer

    def _graph(self, query: str) -> Graph:
        reply = self._query(query, ACCEPT)
        try:
            return parse_graph(reply.content_type, reply.body, self._endpoint)
        except RdfHttpError as error:
            raise SparqlError(error.category) from None

    def construct(self, query: str) -> Graph:
        """Run a ``CONSTRUCT``."""
        return self._graph(query)

    def describe(self, query: str) -> Graph:
        """Run a ``DESCRIBE``."""
        return self._graph(query)

    def update(self, text: str) -> None:
        """Run a SPARQL Update; succeeds on a 2xx answer only."""
        self._send(
            write=True,
            headers={"Content-Type": "application/sparql-update"},
            body=text,
        )


def _row(binding: object) -> dict[str, dict[str, str]]:
    if not isinstance(binding, dict):
        raise SparqlError(Category.NON_JSON_RESPONSE)
    row: dict[str, dict[str, str]] = {}
    for name, cell in binding.items():
        if not isinstance(cell, dict) or "type" not in cell or "value" not in cell:
            raise SparqlError(Category.NON_JSON_RESPONSE)
        kept = {"type": str(cell["type"]), "value": str(cell["value"])}
        if "datatype" in cell:
            kept["datatype"] = str(cell["datatype"])
        language = cell.get("xml:lang")
        if language is not None:
            kept["language"] = str(language)
        row[str(name)] = kept
    return row
