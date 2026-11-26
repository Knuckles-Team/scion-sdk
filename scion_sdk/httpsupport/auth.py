"""Auth header injection strategies for fleet HTTP clients.

CONCEPT:MERISTEM-ECO.ui.fleet-http-client-library — Fleet HTTP Client Library

The fleet's connectors authenticate in four recurring ways; each is one
strategy here instead of a hand-rolled ``_build_headers`` per repo:

* :class:`TokenAuth` — a header token with a configurable scheme prefix
  (``Bearer`` for most APIs, ``SSWS`` for Okta, no prefix for raw API keys
  like Portainer's ``X-API-Key``). Accepts either a static ``token`` or a
  ``token_provider`` callable for OAuth/JWT manager delegation (dockerhub's
  ``TokenManager``, salesforce flows) — the provider is consulted on every
  request, so refresh logic stays in the manager.
* :class:`BasicAuth` — RFC 7617 ``Authorization: Basic`` credentials.
* :class:`QueryApiKeyAuth` — an API key carried as a query parameter.
* :class:`HeaderPairAuth` — one or more service-token headers for connectors
  behind a zero-trust access proxy (``header_auth_from_settings``); composes
  with :class:`TokenAuth` or :class:`BasicAuth` so the proxy token and the
  application credential travel together.
* The :class:`AuthHeaderInjector` base — no credentials (anonymous), and the
  extension point for bespoke schemes.

Strategies expose :meth:`~AuthHeaderInjector.secrets` so the client can feed
its :class:`~scion_sdk.httpsupport.LogRedactor`, and may define an
``invalidate()`` method to opt in to one transparent retry after a 401
(the client duck-types for it).
"""

from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import Callable, Mapping

from scion_sdk.config import setting
from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.credentials.resolution import resolve_secret_reference
from scion_sdk.credentials.resolver import CredentialResolver

__all__ = [
    "AuthHeaderInjector",
    "BasicAuth",
    "HeaderPairAuth",
    "QueryApiKeyAuth",
    "TokenAuth",
    "header_auth_from_settings",
]

#: Header names the application credential and the transport own; a
#: service-token header may never write one of them (case-insensitive).
_RESERVED_HEADER_NAMES = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "host",
        "transfer-encoding",
    }
)


class AuthHeaderInjector:
    """Base auth strategy: contributes headers / query params per request.

    The base class injects nothing (anonymous access). Subclasses override
    :meth:`headers` and/or :meth:`params`; both are re-evaluated on every
    request so live credentials (rotating tokens) stay current.
    """

    def headers(self) -> dict[str, str]:
        """Headers to merge into every request."""
        return {}

    def params(self) -> dict[str, str]:
        """Query parameters to merge into every request."""
        return {}

    def secrets(self) -> list[str]:
        """Literal secret values for log redaction."""
        return []


class TokenAuth(AuthHeaderInjector):
    """Header-token auth with a configurable header name and scheme prefix.

    Args:
        token: A static token value.
        token_provider: A zero-argument callable returning the current token
            (mutually exclusive with ``token``); called per request, so a
            delegated token manager controls caching and refresh.
        header: Header to carry the token (default ``Authorization``).
        prefix: Scheme prefix before the token — ``"Bearer"`` (default),
            ``"SSWS"`` (Okta), or ``None``/``""`` for a bare token value
            (e.g. ``X-API-Key`` style headers).
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        token_provider: Callable[[], str] | None = None,
        header: str = "Authorization",
        prefix: str | None = "Bearer",
    ) -> None:
        if (token is None) == (token_provider is None):
            raise ValueError(
                "TokenAuth requires exactly one of 'token' or 'token_provider'"
            )
        self._token = token
        self._token_provider = token_provider
        self.header = header
        self.prefix = prefix or ""

    def _current_token(self) -> str:
        if self._token_provider is not None:
            return self._token_provider()
        return self._token or ""

    def headers(self) -> dict[str, str]:
        token = self._current_token()
        value = f"{self.prefix} {token}" if self.prefix else token
        return {self.header: value}

    def secrets(self) -> list[str]:
        # Provider-backed tokens are intentionally not minted here: secrets()
        # may run before any request, and the scheme-pattern redaction in
        # LogRedactor already covers "<prefix> <token>" strings.
        return [self._token] if self._token else []


class BasicAuth(AuthHeaderInjector):
    """RFC 7617 ``Authorization: Basic`` username/password credentials."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self._password = password

    def headers(self) -> dict[str, str]:
        credentials = b64encode(f"{self.username}:{self._password}".encode()).decode(
            "ascii"
        )
        return {"Authorization": f"Basic {credentials}"}

    def secrets(self) -> list[str]:
        return [self._password] if self._password else []


class QueryApiKeyAuth(AuthHeaderInjector):
    """API key carried as a query parameter on every request."""

    def __init__(self, param: str, key: str) -> None:
        if not param or not key:
            raise ValueError("QueryApiKeyAuth requires both 'param' and 'key'")
        self.param = param
        self._key = key

    def params(self) -> dict[str, str]:
        return {self.param: self._key}

    def secrets(self) -> list[str]:
        return [self._key]


class HeaderPairAuth(AuthHeaderInjector):
    """Service-token headers for a zero-trust access proxy.

    A connector behind an access proxy sends one or more service-token
    headers on every request, alongside the application credential. Names
    the application credential or the transport owns (``Authorization``,
    ``Host``, ``Cookie``, ``Content-Length``, ``Transfer-Encoding``,
    ``Connection``, matched case-insensitively) are refused, as are names
    and values that hold a line break. Error text names the header only;
    the value is a secret and feeds the client's log redaction.

    Args:
        headers: Header name to resolved value.
    """

    def __init__(self, headers: Mapping[str, str]) -> None:
        self._pairs = {
            name: self._checked(name, value) for name, value in headers.items()
        }

    @staticmethod
    def _checked(name: object, value: object) -> str:
        if not isinstance(name, str) or not name:
            raise ValueError("service-token header name is empty")
        if "\r" in name or "\n" in name:
            raise ValueError(f"service-token header name {name!r} holds a line break")
        if name.lower() in _RESERVED_HEADER_NAMES:
            raise ValueError(f"service-token header name {name!r} is reserved")
        if not isinstance(value, str) or not value:
            raise ValueError(f"service-token header {name!r} holds no value")
        if "\r" in value or "\n" in value:
            raise ValueError(f"service-token header {name!r} holds a line break")
        return value

    def headers(self) -> dict[str, str]:
        return dict(self._pairs)

    def secrets(self) -> list[str]:
        return list(self._pairs.values())

    def __repr__(self) -> str:
        return f"HeaderPairAuth(names={sorted(self._pairs)!r})"


def header_auth_from_settings(
    prefix: str, *, resolver: CredentialResolver | None = None
) -> HeaderPairAuth:
    """Service-token headers from the ``<PREFIX>_ACCESS_HEADERS`` setting.

    The setting is a JSON object mapping header name to value, where a value
    is either a literal or a secret reference (``env://`` or ``openbao://``)
    resolved through ``resolver`` (the environment, plus OpenBao when
    configured). An unset or empty setting yields no headers.

    Raises:
        ValueError: the setting is not a string-to-string JSON object.
    """
    raw = setting(f"{prefix}_ACCESS_HEADERS", "")
    if not raw:
        return HeaderPairAuth({})
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{prefix}_ACCESS_HEADERS must be a JSON object") from error
    if not isinstance(mapping, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in mapping.items()
    ):
        raise ValueError(
            f"{prefix}_ACCESS_HEADERS must map header names to string values"
        )
    resolved: dict[str, str] = {}
    for name, value in mapping.items():
        try:
            parse_secret_reference(value)
        except SecretReferenceError:
            resolved[name] = value
        else:
            resolved[name] = resolve_secret_reference(value, resolver)
    return HeaderPairAuth(resolved)
