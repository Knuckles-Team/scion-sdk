"""Entity reconciliation client (W3C Entity Reconciliation Service API).

SCION-CENTRALIZED-CAPABILITIES-R054. The client returns ranked candidates for one
name and never chooses a match: the engine accepts a link through its own
proposal path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urljoin

import httpx

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.transports._rdf_http import Category, RdfHttp, RdfHttpError

__all__ = ["Candidate", "ReconciliationClient", "ReconciliationError"]

_MAX_BYTES = 8_388_608


class ReconciliationError(RdfHttpError):
    """A reconciliation exchange failed; ``category`` is closed."""


@dataclass(frozen=True)
class Candidate:
    """One candidate entity, in service order."""

    entity: str
    name: str
    description: str
    score: Decimal
    match: bool


class ReconciliationClient:
    """Reconciles one name against a reconciliation service."""

    def __init__(
        self,
        options: HttpClientOptions,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._http = RdfHttp(options, transport=transport)

    def _json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        content: bytes | None = None,
    ) -> object:
        try:
            reply = self._http.send(
                method,
                url,
                write=False,
                max_bytes=_MAX_BYTES,
                headers=headers,
                content=content,
            )
            return json.loads(reply.body)
        except RdfHttpError as error:
            raise ReconciliationError(error.category, error.status_class) from None
        except ValueError:
            raise ReconciliationError(Category.NON_JSON_RESPONSE) from None

    def reconcile(
        self,
        endpoint: str,
        query: str,
        type: str | None = None,
        limit: int | None = None,
    ) -> list[Candidate]:
        """Return ranked candidates for ``query``; an unknown name gives ``[]``."""
        if not query or (type is not None and not type):
            raise ValueError("query and type must be non-empty")
        if limit is not None and not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        manifest = self._json("GET", endpoint, {"Accept": "application/json"})
        space = manifest.get("identifierSpace") if isinstance(manifest, dict) else None
        if not isinstance(space, str) or not space:
            raise ReconciliationError(Category.MANIFEST_INVALID)
        one: dict[str, object] = {"query": query}
        if type is not None:
            one["type"] = type
        if limit is not None:
            one["limit"] = limit
        body = urlencode({"queries": json.dumps({"q0": one})}).encode()
        answer = self._json(
            "POST",
            endpoint,
            {"Content-Type": "application/x-www-form-urlencoded"},
            body,
        )
        return _candidates(answer, space)


def _candidates(answer: object, space: str) -> list[Candidate]:
    entry = answer.get("q0") if isinstance(answer, dict) else None
    results = entry.get("result") if isinstance(entry, dict) else None
    if not isinstance(results, list):
        raise ReconciliationError(Category.NON_JSON_RESPONSE)
    found = []
    for item in results:
        try:
            found.append(
                Candidate(
                    entity=urljoin(space, str(item["id"])),
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "")),
                    score=Decimal(str(item.get("score", 0))),
                    match=bool(item.get("match", False)),
                )
            )
        except (KeyError, AttributeError, TypeError, InvalidOperation):
            raise ReconciliationError(Category.NON_JSON_RESPONSE) from None
    return found
