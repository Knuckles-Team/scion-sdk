"""Build the streamable-HTTP transport of an ``http`` upstream (R914.3).

The URL must use ``https``, or ``http`` on a loopback address. The URL is
checked with :func:`validate_base_url_resolved` (the SDK's egress policy and
its named escape hatches). The credential arrives as a secret reference and is
resolved at connect time into request memory only; TLS comes from
:func:`resolve_tls_profile` — there is no bare verify-off flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.http.egress_validation import validate_base_url_resolved
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_EGRESS_REFUSED,
    UPSTREAM_KIND_FIELDS,
    UpstreamDecl,
    UpstreamError,
)

if TYPE_CHECKING:
    from scion_sdk.credentials.resolver import CredentialResolver

__all__ = ["build_http_transport"]


def _url_problem(url: str) -> str | None:
    """Why the URL violates the https-or-loopback rule, if it does."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme == "https":
        return None
    if parsed.scheme == "http":
        loopback = host in {"127.0.0.1", "::1", "localhost"}
        if loopback:
            return None
        return f"http URL to non-loopback host {host!r}"
    return f"scheme {parsed.scheme!r} is not https"


def build_http_transport(
    decl: UpstreamDecl,
    *,
    resolver: CredentialResolver,
) -> Any:
    """A streamable-HTTP client transport for ``decl``, egress-checked (R914.3).

    Raises:
        UpstreamError: ``UPSTREAM_EGRESS_REFUSED`` when the URL is not
            https-or-loopback or fails the egress policy; the TLS profile is
            unavailable; or the credential reference cannot resolve.
    """
    from fastmcp.client.transports import StreamableHttpTransport

    if decl.url is None:
        raise UpstreamError(UPSTREAM_KIND_FIELDS, f"{decl.name}: http requires url")
    problem = _url_problem(decl.url)
    if problem is not None:
        raise UpstreamError(UPSTREAM_EGRESS_REFUSED, f"{decl.name}: {problem}")
    decision = validate_base_url_resolved(decl.url)
    if not decision.allowed:
        raise UpstreamError(UPSTREAM_EGRESS_REFUSED, f"{decl.name}: {decision.reason}")
    verify: Any = True
    if decl.tls_profile is not None:
        from scion_sdk.tls.resolve import resolve_tls_profile

        try:
            profile = resolve_tls_profile(decl.name, profile_name=decl.tls_profile)
        except Exception:
            raise UpstreamError(
                UPSTREAM_EGRESS_REFUSED, f"{decl.name}: tls profile is unavailable"
            ) from None
        verify = profile.ssl_context
    headers: dict[str, str] | None = None
    if decl.credential is not None:
        try:
            parsed = parse_secret_reference(decl.credential)
        except SecretReferenceError:
            raise UpstreamError(
                UPSTREAM_EGRESS_REFUSED, f"{decl.name}: credential is not a reference"
            ) from None
        try:
            value = resolver.resolve(parsed)
        except Exception:
            raise UpstreamError(
                UPSTREAM_EGRESS_REFUSED,
                f"{decl.name}: credential reference is unavailable",
            ) from None
        headers = {"Authorization": f"Bearer {value}"}
    return StreamableHttpTransport(url=decl.url, headers=headers, verify=verify)
