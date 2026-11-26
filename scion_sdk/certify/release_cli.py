"""The ``connector-release-sign`` console script.

``connector-release-sign --package-root DIR --key-id ID --key-ref REF --lock FILE
-- COMMAND [ARG ...]`` starts the connector over stdio as ``connector-certify``
does, reads its live declaration and ontology, signs their digest and writes
``release.json`` atomically into the package root (SCION-CONNECTOR-CONTROL-R650.1.2).

Exit ``0``: written. ``2``: refused, with the signing error message.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import anyio

from scion_sdk.artifacts.release_inputs import read_release_inputs
from scion_sdk.certify.cli import build_endpoint
from scion_sdk.credentials.provider import CredentialProvider
from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)
from scion_sdk.credentials.resolution import default_credential_resolver
from scion_sdk.manifest.release_lock import load_release_key_lock
from scion_sdk.manifest.release_signature import ReleaseSigningError, sign_release
from scion_sdk.ports.session import TransportEndpoint

__all__ = ["main"]

_logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="connector-release-sign")
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument(
        "--connector", required=True, help="connector name (scopes the key)"
    )
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--key-ref", required=True, help="versioned openbao reference")
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--env", action="append", default=[], metavar="NAME=REFERENCE")
    parser.add_argument("--placeholder", action="append", default=[], metavar="NAME")
    parser.epilog = "Everything after -- is the stdio server command and its arguments."
    return parser


def _write_release_evidence(root: Path, evidence: dict[str, str]) -> Path:
    """Write ``release.json`` into ``root`` atomically."""
    target = root / "release.json"
    body = json.dumps(evidence, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", dir=root, delete=False, encoding="utf-8"
    ) as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    Path(handle.name).replace(target)
    return target


async def _sign(
    endpoint: TransportEndpoint, args: argparse.Namespace
) -> dict[str, str]:
    from scion_sdk.transports.mcp import McpTransport

    trusted = load_release_key_lock(args.lock)
    reference = parse_secret_reference(args.key_ref)
    async with McpTransport().session(endpoint) as session:
        declaration, graph = await read_release_inputs(session)
    provider = CredentialProvider(default_credential_resolver(), args.connector)
    signed = sign_release(
        declaration,
        graph,
        key_id=args.key_id,
        key_reference=reference,
        provider=provider,
        trusted_keys=trusted,
    )
    return {
        "key_id": signed.key_id,
        "digest": signed.digest,
        "signature": signed.signature,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``connector-release-sign``; returns the process exit code."""
    logging.basicConfig(stream=sys.stderr, format="%(message)s", level=logging.INFO)
    items = list(sys.argv[1:] if argv is None else argv)
    split = items.index("--") if "--" in items else len(items)
    args = _parser().parse_args(items[:split])
    args.command = items[split + 1 :]
    namespace = argparse.Namespace(
        command=args.command,
        env=args.env,
        placeholder=args.placeholder,
        timeout=args.timeout,
        url="",
        bearer_token="",
        predecessor_url="",
        predecessor_bearer_token="",
        oidc_issuer="",
        oidc_token_url="",
        oidc_client_id="",
        oidc_client_secret_ref="",
        oidc_audience="",
        oidc_scope="",
    )
    try:
        endpoint = build_endpoint(namespace)
        evidence = anyio.run(_sign, endpoint, args)
        _write_release_evidence(args.package_root, evidence)
    except (ReleaseSigningError, SecretReferenceError) as error:
        _logger.error("connector-release-sign: %s", error)
        return 2
    except Exception:
        _logger.error("connector-release-sign cannot sign this connector")
        return 2
    return 0
