"""Validated timeout and OIDC options for connector certification."""

from __future__ import annotations

import argparse
import math

from scion_sdk.auth.client_credentials import ClientCredentialsAuth
from scion_sdk.auth.oidc import (
    ClientCredentialsConfig,
    client_credentials_auth,
)
from scion_sdk.auth.pkce import PkceBearerAuth, PkcePorts


def _positive_timeout(value: str) -> float:
    """An argparse type accepting only finite positive seconds."""
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return timeout


def _pkce_auth(args: argparse.Namespace) -> PkceBearerAuth:
    """Build PKCE auth from the host-supplied ceremony ports (``--auth pkce``)."""
    ports = getattr(args, "pkce_ports", None)
    if not isinstance(ports, PkcePorts):
        raise ValueError("--auth pkce needs the host broker ports")
    return PkceBearerAuth.from_ports(ports)


def _oidc_auth(
    args: argparse.Namespace,
) -> ClientCredentialsAuth | PkceBearerAuth | None:
    """Build SDK auth: PKCE for ``--auth pkce``, client credentials when any OIDC option is present."""
    if getattr(args, "auth", "") == "pkce":
        return _pkce_auth(args)
    values = (
        args.oidc_issuer,
        args.oidc_token_url,
        args.oidc_client_id,
        args.oidc_client_secret_ref,
        args.oidc_audience,
        args.oidc_scope,
    )
    if not any(values):
        return None
    config = ClientCredentialsConfig(
        issuer=args.oidc_issuer,
        token_url=args.oidc_token_url,
        client_id=args.oidc_client_id,
        client_secret_ref=args.oidc_client_secret_ref,
        audience=args.oidc_audience,
        scope=args.oidc_scope,
    )
    return client_credentials_auth(config, timeout_seconds=args.timeout)
