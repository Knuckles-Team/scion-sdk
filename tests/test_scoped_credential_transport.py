"""Scoped credential transport: the exact grant, per request (SCION-CONNECTOR-CONTROL-R035.2-R035.6)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from credential_support import make_binding
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from scion_sdk.credentials.binding import (
    CredentialAccessBinding,
    current_credential_binding,
)
from scion_sdk.credentials.provider import (
    CredentialProvider,
    CredentialRefused,
    CredentialScopeError,
)
from scion_sdk.credentials.references import parse_secret_reference
from scion_sdk.identity import ActorContext
from scion_sdk.mcp.actor_binding import ActorBindingMiddleware
from scion_sdk.mcp.tool_surface import register_tool_surface

SECRET = "s3cr3t-value-zz9"  # sanitizer:ignore - synthetic fixture value
REF = parse_secret_reference("openbao://apps/vault-agent#TOKEN")
NOW = 1_000


class _Resolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, reference: object) -> str:
        self.calls += 1
        return SECRET


class _Authority:
    def __init__(self, verdict: str = "ok") -> None:
        self.verdict = verdict
        self.calls = 0

    def verify(self, binding: CredentialAccessBinding) -> str:
        self.calls += 1
        if self.verdict == "boom":
            raise RuntimeError("down")
        return self.verdict


def _provider(
    authority: _Authority | None = None, connector: str = "vault-agent"
) -> tuple[CredentialProvider, _Resolver, _Authority]:
    resolver, authority = _Resolver(), authority or _Authority()
    provider = CredentialProvider(
        resolver=resolver,
        connector=connector,
        authority=authority,
        clock_ms=lambda: NOW,
    )
    return provider, resolver, authority


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R035.2")
def test_exact_grant_required_and_values_discarded() -> None:
    provider, resolver, _ = _provider()
    with pytest.raises(CredentialRefused) as caught:
        provider.get_secret(REF)
    assert caught.value.reason == "binding_mismatch" and resolver.calls == 0
    with pytest.raises(CredentialRefused) as caught:
        provider.get_secret(REF, make_binding(connector="forged-agent"))
    assert caught.value.reason == "binding_mismatch" and resolver.calls == 0
    seen: list[str] = []
    assert provider.inject(REF, lambda v: seen.append(v) or "sent", make_binding()) == (
        "sent"
    )
    assert seen == [SECRET] and resolver.calls == 1
    assert SECRET not in repr(provider) and SECRET not in json.dumps(
        [r.model_dump() for r in provider.records]
    )


def _served(provider: CredentialProvider, seen: list[Any]) -> FastMCP[Any]:
    mcp: FastMCP[Any] = FastMCP("cred")
    mcp.add_middleware(ActorBindingMiddleware())

    def register_vault_tools(server: FastMCP[Any]) -> None:
        @server.tool
        def use_credential() -> str:
            seen.append(current_credential_binding())
            provider.get_secret(REF)
            return "used"

    def binder(actor: ActorContext, tool: str) -> CredentialAccessBinding | None:
        if actor.actor_id == "intruder":
            return None
        return make_binding(operation=tool, requesting_principal=actor.actor_id)

    register_tool_surface(
        mcp, service="cred", registrars=[register_vault_tools], credential_binder=binder
    )
    return mcp


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R035.3")
async def test_registration_binds_verified_context() -> None:
    provider, resolver, authority = _provider()
    seen: list[Any] = []
    async with Client(_served(provider, seen)) as client:
        result = await client.call_tool("use_credential", {})
    assert result.data == "used" and authority.calls == 1 and resolver.calls == 1
    (binding,) = seen
    assert binding.operation == "use_credential"
    assert binding.requesting_principal  # taken from the verified actor
    assert current_credential_binding() is None


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R035.5")
def test_use_record_joins_receipts_and_excludes_secrets() -> None:
    provider, _, _ = _provider()
    binding = make_binding()
    provider.get_secret(REF, binding)
    with pytest.raises(CredentialScopeError):
        provider.get_secret(parse_secret_reference("openbao://apps/other#T"), binding)
    ok, refused = provider.records
    assert (ok.outcome, refused.outcome) == ("resolved", "scope_denied")
    assert len(ok.binding_digest) == 64 and ok.grant_revision == "r1"
    assert (ok.tenant, ok.client, ok.connector) == ("t1", "client-a", "vault-agent")
    dumped = json.dumps([r.model_dump() for r in provider.records])
    assert SECRET not in dumped and "password" not in dumped


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R035.6")
async def test_served_bound_credential_resolution() -> None:
    provider, resolver, _authority = _provider()
    async with Client(_served(provider, [])) as client:
        assert (await client.call_tool("use_credential", {})).data == "used"
    assert resolver.calls == 1
    # a wrong tenant is refused by the authority: no resolver call
    revoked, revoked_resolver, _ = _provider(_Authority("revoked"))
    async with Client(_served(revoked, [])) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("use_credential", {})
    assert "revoked" in str(caught.value) and SECRET not in str(caught.value)
    assert revoked_resolver.calls == 0
    # a separate connector grant permits only its own transport
    other, other_resolver, _ = _provider(connector="other-agent")
    async with Client(_served(other, [])) as client:
        with pytest.raises(ToolError):
            await client.call_tool("use_credential", {})
    assert other_resolver.calls == 0
    # an unavailable authority keeps refusing
    down, down_resolver, _ = _provider(_Authority("boom"))
    async with Client(_served(down, [])) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("use_credential", {})
    assert "authority_unavailable" in str(caught.value) and down_resolver.calls == 0


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R046.6")
async def test_served_inline_pkce_bounds() -> None:
    from pkce_support import make_manager

    from scion_sdk.auth.pkce import PkceAuthRefusedError

    manager, _, transport, _ = make_manager()
    transport.silent = True
    mcp: FastMCP[Any] = FastMCP("pkce")

    @mcp.tool
    async def needs_login() -> str:
        try:
            await manager.authenticate(
                make_binding(), operation_digest="d" * 64, interactive=True
            )
        except PkceAuthRefusedError as refused:
            return refused.reason
        return "authenticated"

    async with Client(mcp) as client:
        assert (await client.call_tool("needs_login", {})).data == "input_timeout"
    assert transport.timeouts == [120.0]  # bounded, never blocking on stdin


async def test_binding_context_and_middleware_are_public_seams() -> None:
    from scion_sdk.credentials.binding import (
        CredentialUseRecord,
        use_credential_binding,
    )
    from scion_sdk.credentials.provider import CredentialAuthorityPort
    from scion_sdk.mcp.credential_context import (
        CredentialBinder,
        CredentialBindingMiddleware,
        bind_credential_context,
    )

    provider, resolver, authority = _provider()
    port: CredentialAuthorityPort = authority
    assert port is authority
    binding = make_binding()
    with use_credential_binding(binding):
        assert provider.get_secret(REF) == SECRET  # the context binding is used
    assert current_credential_binding() is None and resolver.calls == 1
    assert provider.records[0].outcome == "resolved"
    assert isinstance(provider.records[0], CredentialUseRecord)
    mcp: FastMCP[Any] = FastMCP("direct")
    binder: CredentialBinder = lambda actor, tool: binding  # noqa: E731
    bind_credential_context(mcp, binder)
    assert any(isinstance(m, CredentialBindingMiddleware) for m in mcp.middleware)
