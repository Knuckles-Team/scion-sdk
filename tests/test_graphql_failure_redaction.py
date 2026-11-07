"""Upstream GraphQL exceptions are not returned or logged."""

from __future__ import annotations

import logging
import traceback

import pytest

from scion_sdk.mcp.graphql import (
    ctx_graphql_get_type_details,
    ctx_graphql_list_types,
)
from scion_sdk.tls.resolve import resolve_tls_profile
from scion_sdk.transports.graphql import GraphQLClient, GraphQLExecutionError

_MARKER = "private-upstream-marker"


class _FailingClient:
    def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError(_MARKER)


def _client() -> GraphQLClient:
    client = GraphQLClient(
        "https://example.invalid/graphql",
        tls_profile=resolve_tls_profile("graphql"),
        max_retries=0,
    )
    client._client = _FailingClient()
    return client


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R006.4")
def test_graphql_sync_failure_does_not_expose_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(GraphQLExecutionError) as caught:
        _client().execute("query { x }")
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert _MARKER not in caplog.text
    assert _MARKER not in "".join(traceback.format_exception(caught.value))
    assert _MARKER not in str(caught.value.payload())
    assert caught.value.payload()["error_class"] == "RuntimeError"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R006.4")
async def test_graphql_async_and_introspection_hide_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(GraphQLExecutionError) as caught:
        await _client().execute_async("query { x }")

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError(_MARKER)

    failures = [
        await ctx_graphql_list_types(fail),
        await ctx_graphql_get_type_details(fail, "X"),
    ]
    assert all(value["error_class"] == "RuntimeError" for value in failures)
    assert _MARKER not in str(failures)
    assert _MARKER not in caplog.text
    assert _MARKER not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class _QueryError(Exception):
    """Named like the protocol library's query error; carries ``errors``."""

    def __init__(self, errors: list[dict[str, object]], code: int | None = None):
        super().__init__(_MARKER)
        self.errors = errors
        self.code = code


_QueryError.__name__ = "TransportQueryError"


def _raising(exc: Exception, **options: object) -> GraphQLClient:
    client = _client()
    for name, value in options.items():
        setattr(client, name, value)

    class _Raises:
        def execute(self, *args: object, **kwargs: object) -> object:
            raise exc

    client._client = _Raises()
    return client


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R006.6")
@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("NOT_FOUND", "not_found"),
        ("FORBIDDEN", "forbidden"),
        ("BAD_USER_INPUT", "invalid_input"),
        ("CONFLICT", "conflict"),
        ("INTERNAL_SERVER_ERROR", "server_error"),
        ("SOMETHING_ELSE", "unknown"),
    ],
)
def test_query_error_category_is_typed_without_server_text(
    code: str, category: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    errors = [{"message": _MARKER, "extensions": {"code": code}, "path": ["x"]}]
    with pytest.raises(GraphQLExecutionError) as caught:
        _raising(_QueryError(errors)).execute("query { x }")
    error = caught.value
    assert error.category == category
    assert str(error) == f"Query execution failed: TransportQueryError ({category})"
    payload = error.payload()
    assert payload["category"] == category and payload["error_count"] == 1
    assert payload["retryable"] is False
    assert _MARKER not in str(payload) + caplog.text
    assert _MARKER not in "".join(traceback.format_exception(error))


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R006.6")
def test_query_error_without_codes_uses_status_then_classifier() -> None:
    plain = [{"message": _MARKER}]
    with pytest.raises(GraphQLExecutionError) as by_status:
        _raising(_QueryError(plain, code=404)).execute("query { x }")
    assert by_status.value.category == "not_found"

    def classify(entries: list[dict[str, object]]) -> str:
        return "not_found" if entries else "unknown"

    with pytest.raises(GraphQLExecutionError) as by_classifier:
        _raising(_QueryError(plain), _classifier=classify).execute("query { x }")
    assert by_classifier.value.category == "not_found"

    def bad(entries: list[dict[str, object]]) -> str:
        raise RuntimeError(_MARKER)

    with pytest.raises(GraphQLExecutionError) as broken:
        _raising(_QueryError(plain), _classifier=bad).execute("query { x }")
    assert broken.value.category == "unknown"
    assert _MARKER not in str(broken.value.payload())


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R006.6")
def test_transport_failure_has_no_category() -> None:
    with pytest.raises(GraphQLExecutionError) as caught:
        _raising(RuntimeError(_MARKER)).execute("query { x }")
    assert caught.value.category is None
    assert "category" not in caught.value.payload()
