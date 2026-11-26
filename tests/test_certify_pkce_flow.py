"""connector-certify authenticates through PKCE (SCION-CONNECTOR-CONTROL-R046.3, R046.6, R046)."""

from __future__ import annotations

import argparse

import httpx
import pytest
from credential_support import make_binding
from pkce_support import TOKEN, make_manager

from scion_sdk.auth.pkce import PkceAuthRefusedError, PkceBearerAuth, PkcePorts
from scion_sdk.certify.cli import parse_arguments
from scion_sdk.certify.options import _oidc_auth


def _ports(manager, held: list[str]) -> PkcePorts:  # type: ignore[no-untyped-def]
    async def load() -> str | None:
        return held[0] if held else None

    return PkcePorts(
        manager=manager,
        binding=make_binding(),
        operation_digest="d" * 64,
        load_token=load,
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.3")
def test_oidc_auth_selects_pkce_branch() -> None:
    args = parse_arguments(["--url", "https://x.example.test/mcp", "--auth", "pkce"])
    assert args.auth == "pkce"
    with pytest.raises(ValueError, match="host broker ports"):
        _oidc_auth(args)
    manager, _, _, _ = make_manager()
    args.pkce_ports = _ports(manager, [])
    assert isinstance(_oidc_auth(args), PkceBearerAuth)
    assert _oidc_auth(parse_arguments(["--url", "https://x.example.test/mcp"])) is None
    assert isinstance(argparse.Namespace(), argparse.Namespace)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.6")
async def test_certify_run_authenticates_through_pkce() -> None:
    manager, issuer, _, broker = make_manager()
    held: list[str] = []

    class _Persisting:
        async def claim(self, ref: str) -> bool:
            return await broker.claim(ref)

        async def persist(self, challenge, token, token_ref):  # type: ignore[no-untyped-def]
            held.append(token["access_token"])
            return await broker.persist(challenge, token, token_ref)

    manager._broker = _Persisting()  # type: ignore[assignment]
    auth = PkceBearerAuth.from_ports(_ports(manager, held))
    seen: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), auth=auth
    ) as client:
        assert (await client.get("https://api.example.test/x")).status_code == 200
        assert (await client.get("https://api.example.test/x")).status_code == 200
    assert seen == [f"Bearer {TOKEN}"] * 2
    assert issuer.exchanges == 1  # the second request reuses the persisted token
    assert manager.records[0].outcome == "persisted"
    # failed persistence resumes no operation
    failing, _, _, fbroker = make_manager()
    fbroker.no_receipt = True
    bad = PkceBearerAuth.from_ports(_ports(failing, []))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), auth=bad
    ) as client:
        with pytest.raises(PkceAuthRefusedError) as caught:
            await client.get("https://api.example.test/x")
    assert caught.value.reason == "persistence_failed" and len(seen) == 2
