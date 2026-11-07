"""Support types and helpers for :mod:`scion_sdk.transports.graphql`."""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scion_sdk.exceptions import ParameterError

_logger = logging.getLogger("scion_sdk.transports.graphql")

#: HTTP statuses retried with backoff; every other failure fails at once.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class GraphQLAuth:
    """One auth header. ``repr`` never shows the credential."""

    header: str = ""
    value: str = field(default="", repr=False)

    @classmethod
    def none(cls) -> GraphQLAuth:
        return cls()

    @classmethod
    def bearer(cls, token: str) -> GraphQLAuth:
        """``Authorization: Bearer <token>``."""
        return cls("Authorization", f"Bearer {token}")

    @classmethod
    def token(
        cls, token: str, *, header: str = "Authorization", prefix: str = "token"
    ) -> GraphQLAuth:
        """``<header>: <prefix> <token>``; an empty prefix sends the raw token."""
        return cls(header, f"{prefix} {token}" if prefix else token)

    @classmethod
    def basic(cls, username: str, password: str) -> GraphQLAuth:
        """``Authorization: Basic base64(username:password)``."""
        raw = f"{username}:{password}".encode()
        return cls("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))

    def headers(self) -> dict[str, str]:
        return {self.header: self.value} if self.header and self.value else {}


#: The closed set of query-error categories.
_CATEGORIES = frozenset(
    {"not_found", "forbidden", "invalid_input", "conflict", "server_error", "unknown"}
)

#: ``extensions.code`` values (upper-cased) mapped to a category.
_CODE_CATEGORY = {
    "NOT_FOUND": "not_found",
    "FORBIDDEN": "forbidden",
    "UNAUTHENTICATED": "forbidden",
    "UNAUTHORIZED": "forbidden",
    "BAD_USER_INPUT": "invalid_input",
    "BAD_REQUEST": "invalid_input",
    "INVALID_INPUT": "invalid_input",
    "GRAPHQL_VALIDATION_FAILED": "invalid_input",
    "CONFLICT": "conflict",
    "INTERNAL_SERVER_ERROR": "server_error",
    "INTERNAL": "server_error",
}

#: HTTP statuses mapped to a category when no error entry carries a code.
_STATUS_CATEGORY = {
    400: "invalid_input",
    401: "forbidden",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "invalid_input",
}

#: Most error entries counted in a payload.
_ERROR_COUNT_CAP = 20

ErrorClassifier = Callable[[list[dict[str, Any]]], str]


def _error_entries(exc: BaseException) -> list[dict[str, Any]] | None:
    """The top-level ``errors`` list of a query error, else ``None``."""
    errors = getattr(exc, "errors", None)
    if type(exc).__name__ != "TransportQueryError" or not isinstance(errors, list):
        return None
    return [entry for entry in errors if isinstance(entry, dict)]


def _category_of(
    exc: BaseException,
    entries: list[dict[str, Any]],
    classifier: ErrorClassifier | None,
) -> str:
    """One closed category from structured fields only; never server text."""
    for entry in entries:
        extensions = entry.get("extensions")
        code = extensions.get("code") if isinstance(extensions, dict) else None
        if isinstance(code, str) and code.upper() in _CODE_CATEGORY:
            return _CODE_CATEGORY[code.upper()]
    status = _status_of(exc)
    if status is not None:
        if status in _STATUS_CATEGORY:
            return _STATUS_CATEGORY[status]
        if 500 <= status < 600:
            return "server_error"
    if classifier is not None:
        try:
            chosen = classifier(entries)
        except Exception:
            return "unknown"
        return chosen if chosen in _CATEGORIES else "unknown"
    return "unknown"


class GraphQLExecutionError(ParameterError):
    """A redacted GraphQL failure: class, category and correlation id only."""

    def __init__(
        self,
        exc: BaseException,
        *,
        retryable: bool = False,
        classifier: ErrorClassifier | None = None,
    ) -> None:
        self.error_class = type(exc).__name__
        entries = _error_entries(exc)
        self.category = (
            None if entries is None else _category_of(exc, entries, classifier)
        )
        self.error_count = (
            None if entries is None else min(len(entries), _ERROR_COUNT_CAP)
        )
        self.correlation_id = uuid.uuid4().hex
        self.retryable = retryable
        _logger.warning(
            "GraphQL execution failed (correlation_id=%s, error_class=%s)",
            self.correlation_id,
            self.error_class,
        )
        suffix = "" if self.category is None else f" ({self.category})"
        super().__init__(f"Query execution failed: {self.error_class}{suffix}")

    def payload(self) -> dict[str, Any]:
        """The public failure payload, shaped like ``graphql_failure_payload``."""
        extra: dict[str, Any] = (
            {}
            if self.category is None
            else {"category": self.category, "error_count": self.error_count}
        )
        return {
            **extra,
            "status": "error",
            "code": "operation_failed",
            "retryable": self.retryable,
            "error_class": self.error_class,
            "correlation_id": self.correlation_id,
        }


def _status_of(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _retry_or_failure(
    exc: BaseException,
    attempt: int,
    *,
    max_retries: int,
    backoff: float,
    sleep: Callable[[float], None],
    classifier: ErrorClassifier | None = None,
) -> GraphQLExecutionError | None:
    """Back off and return ``None`` to retry, else the redacted failure to raise."""
    retryable = _status_of(exc) in RETRYABLE_STATUS
    if retryable and attempt < max_retries:
        sleep(backoff * (2**attempt))
        return None
    return GraphQLExecutionError(exc, retryable=retryable, classifier=classifier)
