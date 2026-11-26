"""Signed-release fixtures shared by the runner tests (SCION-CONNECTOR-CONTROL-R650.1)."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scion_sdk.artifacts.release_inputs import read_release_inputs
from scion_sdk.credentials.provider import CredentialProvider
from scion_sdk.credentials.references import parse_secret_reference
from scion_sdk.manifest.release_lock import create_release_key_lock
from scion_sdk.manifest.release_signature import sign_release
from scion_sdk.ports.session import McpSession, TransportEndpoint

RELEASE_URI = "connector://release"
KEY_ID = "release-key-1"
SEED = bytes(range(32))
PUBLIC = (
    Ed25519PrivateKey.from_private_bytes(SEED)
    .public_key()
    .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
)
TRUSTED = {KEY_ID: PUBLIC}


class _Resolver:
    def resolve(self, reference: object) -> str:
        return base64.urlsafe_b64encode(SEED).decode().rstrip("=")


def write_lock(path: Path) -> Path:
    """A release lock pinning the shared test signer."""
    if not path.exists():
        create_release_key_lock(path, {"artifact": (KEY_ID, PUBLIC)})
    return path


async def release_evidence(session: McpSession, connector: str) -> dict[str, str]:
    """Sign the session's live declaration and ontology with the test key."""
    declaration, graph = await read_release_inputs(session)
    signed = sign_release(
        declaration,
        graph,
        key_id=KEY_ID,
        key_reference=parse_secret_reference("openbao://apps/fixture/sign#seed@1"),
        provider=CredentialProvider(_Resolver(), "fixture"),
        trusted_keys=TRUSTED,
    )
    return {
        "key_id": signed.key_id,
        "digest": signed.digest,
        "signature": signed.signature,
    }


class SignedSession:
    """Serves ``connector://release`` signed over whatever the inner session serves."""

    def __init__(self, inner: McpSession, connector: str) -> None:
        self._inner = inner
        self._connector = connector

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def watch(self, resource_uris: Any) -> bool:
        return await self._inner.watch(resource_uris)  # type: ignore[attr-defined,no-any-return]

    async def next_changes(self) -> Any:
        return await self._inner.next_changes()  # type: ignore[attr-defined]

    async def list_resources(self) -> list[Any]:
        found = list(await self._inner.list_resources())
        found.append(SimpleNamespace(uri=RELEASE_URI, name=RELEASE_URI))
        return found

    async def read_resource(self, uri: str) -> str:
        if uri == RELEASE_URI:
            return json.dumps(await release_evidence(self._inner, self._connector))
        return await self._inner.read_resource(uri)


class SigningTransport:
    """A transport whose sessions serve a valid signed release."""

    name = "signing"

    def __init__(self, inner: Any, connector_of: Any = None) -> None:
        self._inner = inner
        self._connector_of = connector_of

    def session(
        self, endpoint: TransportEndpoint
    ) -> AbstractAsyncContextManager[McpSession]:
        @asynccontextmanager
        async def open_session() -> AsyncIterator[McpSession]:
            async with self._inner.session(endpoint) as session:
                name = (await session.server_identity()).name
                yield SignedSession(session, name)  # type: ignore[misc]

        return open_session()
