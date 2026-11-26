"""The shared request core of the Linked Data, SPARQL and reconciliation clients.

One governed HTTP client (``build_http_client``) carries every request. A failure
is reduced to a closed category and a status class; no server body, request body
or header value ever reaches an exception message.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from scion_sdk.http.bodies import read_bounded
from scion_sdk.http.client import build_http_client
from scion_sdk.http.errors import (
    HttpProblemError,
    ResponseTooLargeError,
)
from scion_sdk.http.options import HttpClientOptions

__all__ = ["Category", "RdfHttp", "RdfHttpError", "Reply"]

_TRANSPORT_ONLY = (HttpProblemError,)


class Category(StrEnum):
    """The closed failure categories shared by the three clients."""

    NON_RDF_RESPONSE = "NON_RDF_RESPONSE"
    NON_JSON_RESPONSE = "NON_JSON_RESPONSE"
    WRITE_REJECTED = "WRITE_REJECTED"
    READ_REJECTED = "READ_REJECTED"
    TRANSPORT_FAILED = "TRANSPORT_FAILED"
    QUERY_REFUSED = "QUERY_REFUSED"
    RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
    MANIFEST_INVALID = "MANIFEST_INVALID"


class RdfHttpError(Exception):
    """A failed exchange: a closed ``category``; the message holds nothing else."""

    def __init__(self, category: Category, status_class: str = "") -> None:
        text = category.value + (f" ({status_class})" if status_class else "")
        super().__init__(text)
        self.category = category
        self.status_class = status_class


@dataclass(frozen=True)
class Reply:
    """A successful (2xx) answer."""

    status: int
    content_type: str
    location: str
    body: bytes


class RdfHttp:
    """Sends one request through the governed client and classifies the outcome."""

    def __init__(
        self,
        options: HttpClientOptions,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = build_http_client(options, transport=transport)

    def send(
        self,
        method: str,
        url: str,
        *,
        write: bool,
        max_bytes: int,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        data: dict[str, Any] | None = None,
        too_large: Category = Category.NON_RDF_RESPONSE,
        refuse_400: bool = False,
    ) -> Reply:
        """Return the 2xx reply or raise a categorised :class:`RdfHttpError`."""
        rejected = Category.WRITE_REJECTED if write else Category.READ_REJECTED
        try:
            with self._client.stream(
                method, url, headers=headers, content=content, data=data
            ) as response:
                status = response.status_code
                if not 200 <= status < 300:
                    refused = status == 400 and refuse_400 and not write
                    raise RdfHttpError(
                        Category.QUERY_REFUSED if refused else rejected,
                        f"{status // 100}xx",
                    )
                body = read_bounded(response, max_bytes)
                return Reply(
                    status,
                    response.headers.get("content-type", ""),
                    response.headers.get("location", ""),
                    body,
                )
        except RdfHttpError:
            raise
        except ResponseTooLargeError:
            raise RdfHttpError(too_large) from None
        except _TRANSPORT_ONLY:
            raise RdfHttpError(Category.TRANSPORT_FAILED) from None
        except (httpx.HTTPError, ValueError):
            raise RdfHttpError(Category.TRANSPORT_FAILED) from None
