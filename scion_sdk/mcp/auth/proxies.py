"""OAuth proxy, OIDC proxy and remote OAuth providers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp.server.auth import OAuthProxy, RemoteAuthProvider
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from pydantic import AnyHttpUrl

from scion_sdk.config import csv_values
from scion_sdk.mcp.auth.policy import (
    AuthConfigurationError,
    AuthInputs,
    allowed_redirect_uris,
    resolve_auth_secret,
    secure_auth_url,
)
from scion_sdk.mcp.auth.verifiers import hardened_jwt_verifier

__all__ = ["configure_proxy", "configure_remote_oauth"]

_MAX_AUTHORIZATION_SERVERS = 16


def _require(inputs: AuthInputs, names: tuple[str, ...], mode: str) -> None:
    missing = [name for name in names if not getattr(inputs.args, name)]
    if missing:
        raise AuthConfigurationError(f"{mode} requires {', '.join(missing)}")


def _upstream_verifier(inputs: AuthInputs) -> Any:
    return hardened_jwt_verifier(
        jwks_uri=secure_auth_url(inputs.args.token_jwks_uri, field="JWKS URI"),
        issuer=secure_auth_url(inputs.args.token_issuer, field="JWT issuer"),
        audience=inputs.args.token_audience,
    )


@dataclass(frozen=True)
class _ProxyFlow:
    """What differs between the proxy flows; the build steps are shared."""

    mode: str
    proxy: type
    required: tuple[str, ...]
    #: Upstream endpoint keyword -> (argument name, error label), in order.
    endpoints: tuple[tuple[str, str, str], ...]
    client_id: str
    credential_arg: str
    credential_label: str
    credential_keyword: str
    base_url: str
    base_url_label: str
    flow_settings: Callable[[AuthInputs], dict[str, Any]]


_FLOWS: dict[str, _ProxyFlow] = {
    "oauth": _ProxyFlow(
        mode="oauth-proxy",
        proxy=OAuthProxy,
        required=(
            "oauth_upstream_auth_endpoint",
            "oauth_upstream_token_endpoint",
            "oauth_upstream_client_id",
            "oauth_upstream_client_secret_ref",
            "oauth_base_url",
            "token_jwks_uri",
            "token_issuer",
            "token_audience",
        ),
        endpoints=(
            (
                "upstream_authorization_endpoint",
                "oauth_upstream_auth_endpoint",
                "OAuth authorization endpoint",
            ),
            (
                "upstream_token_endpoint",
                "oauth_upstream_token_endpoint",
                "OAuth token endpoint",
            ),
        ),
        client_id="oauth_upstream_client_id",
        credential_arg="oauth_upstream_client_secret_ref",
        credential_label="OAuth client secret",
        credential_keyword="upstream_client_secret",
        base_url="oauth_base_url",
        base_url_label="OAuth base URL",
        flow_settings=lambda inputs: {"token_verifier": _upstream_verifier(inputs)},
    ),
    "oidc": _ProxyFlow(
        mode="oidc-proxy",
        proxy=OIDCProxy,
        required=(
            "oidc_config_url",
            "oidc_client_id",
            "oidc_client_secret_ref",
            "oidc_base_url",
            "token_audience",
        ),
        endpoints=(("config_url", "oidc_config_url", "OIDC configuration URL"),),
        client_id="oidc_client_id",
        credential_arg="oidc_client_secret_ref",
        credential_label="OIDC client secret",
        credential_keyword="client_secret",
        base_url="oidc_base_url",
        base_url_label="OIDC base URL",
        flow_settings=lambda inputs: {"audience": inputs.args.token_audience},
    ),
}


def configure_proxy(inputs: AuthInputs, flow: str) -> Any:
    """The OAuth (``"oauth"``) or OIDC (``"oidc"``) proxy for the configured flow."""
    spec = _FLOWS[flow]
    _require(inputs, spec.required, spec.mode)
    args = inputs.args
    settings: dict[str, Any] = {
        keyword: secure_auth_url(getattr(args, name), field=label)
        for keyword, name, label in spec.endpoints
    }
    settings["upstream_client_id" if flow == "oauth" else "client_id"] = getattr(
        args, spec.client_id
    )
    settings[spec.credential_keyword] = resolve_auth_secret(
        getattr(args, spec.credential_arg), inputs, what=spec.credential_label
    )
    settings.update(spec.flow_settings(inputs))
    settings["base_url"] = secure_auth_url(
        getattr(args, spec.base_url), field=spec.base_url_label
    )
    settings["allowed_client_redirect_uris"] = allowed_redirect_uris(args)
    return spec.proxy(**settings)


def configure_remote_oauth(inputs: AuthInputs) -> RemoteAuthProvider:
    """Token verification against remote authorization servers."""
    _require(
        inputs,
        (
            "remote_auth_servers",
            "remote_base_url",
            "token_jwks_uri",
            "token_issuer",
            "token_audience",
        ),
        "remote-oauth",
    )
    servers = [
        secure_auth_url(value, field="authorization server")
        for value in csv_values(inputs.args.remote_auth_servers)
    ]
    if len(servers) > _MAX_AUTHORIZATION_SERVERS:
        raise AuthConfigurationError("too many remote authorization servers")
    return RemoteAuthProvider(
        token_verifier=_upstream_verifier(inputs),
        authorization_servers=[AnyHttpUrl(server) for server in servers],
        base_url=secure_auth_url(
            inputs.args.remote_base_url, field="remote OAuth base URL"
        ),
    )
