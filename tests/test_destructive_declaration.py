"""One destructive declaration, one guard (SCION-CONNECTOR-CONTROL-R930)."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from fastmcp import Client, Context, FastMCP

from scion_sdk.mcp.destructive import (
    ALLOW_DESTRUCTIVE_SETTING,
    CONFIRM_PARAMETER,
    declared_destructive_actions,
    destructive_actions,
    destructive_operation,
)
from scion_sdk.mcp.method_tools import register_method_tools
from scion_sdk.mcp.tool_surface import register_tool_surface

EFFECTS: list[str] = []


class _Client:
    def ping(self) -> str:
        """Ping the provider."""
        return "pong"


def _registrar(mcp: FastMCP[Any]) -> None:
    @mcp.tool()
    @destructive_actions("suspend")
    async def accounts(
        action: Literal["list", "suspend"],
        ctx: Context | None = None,
        confirm: bool = False,
    ) -> Any:
        """Manage accounts."""
        EFFECTS.append(action)
        return action


def _served() -> FastMCP[Any]:
    mcp: FastMCP[Any] = FastMCP("declared")
    register_tool_surface(
        mcp,
        service="declared",
        client_cls=_Client,
        get_client=_Client,
        registrars=[_registrar],
    )
    return mcp


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    EFFECTS.clear()
    monkeypatch.delenv(ALLOW_DESTRUCTIVE_SETTING, raising=False)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R930")
async def test_declaration_becomes_destructive_hint_and_confirm_argument() -> None:
    async with Client(_served()) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert tools["accounts_suspend"].annotations.destructive_hint is True
    assert tools["accounts_list"].annotations.destructive_hint is False
    assert CONFIRM_PARAMETER in tools["accounts_suspend"].input_schema["properties"]
    assert declared_destructive_actions(_registrar) == frozenset()


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R930.1")
async def test_undeclared_action_runs_and_declared_action_needs_confirmation() -> None:
    async with Client(_served()) as client:
        await client.call_tool("accounts_list", {})
        refused = await client.call_tool("accounts_suspend", {})
        assert EFFECTS == ["list"]
        assert refused.structured_content["cancelled"] is True
        await client.call_tool("accounts_suspend", {"confirm": True})
    assert EFFECTS == ["list", "suspend"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R930.2")
async def test_operator_setting_allows_unattended_destructive_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALLOW_DESTRUCTIVE_SETTING, "true")
    async with Client(_served()) as client:
        await client.call_tool("accounts_suspend", {})
    assert EFFECTS == ["suspend"]


class _Api:
    @destructive_operation
    async def suspend_user(self, user_id: str) -> str:
        """Suspend a user."""
        EFFECTS.append(f"suspend:{user_id}")
        return "ok"

    async def get_user(self, user_id: str) -> str:
        """Read a user."""
        return user_id


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R930.3")
async def test_declared_client_method_is_destructive_and_asks_first() -> None:
    mcp: FastMCP[Any] = FastMCP("methods")
    register_method_tools(mcp, _Api, _Api, prefix="acme")
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["acme_suspend_user"].annotations.destructive_hint is True
        assert tools["acme_get_user"].annotations.destructive_hint is False
        refused = await client.call_tool("acme_suspend_user", {"user_id": "u1"})
        assert EFFECTS == [] and refused.structured_content["cancelled"] is True
        await client.call_tool("acme_suspend_user", {"user_id": "u1", "confirm": True})
    assert EFFECTS == ["suspend:u1"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R930.3")
@pytest.mark.parametrize(
    "name", ["revoke_token", "terminate_session", "wipe_device", "unassign_user"]
)
def test_the_shared_destructive_word_list_classifies_derived_methods(name: str) -> None:
    from scion_sdk.mcp.action_dispatch import is_destructive_action

    assert is_destructive_action(name) is True
    assert is_destructive_action("get_token") is False


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R930.2")
def test_operator_setting_and_method_declaration_are_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scion_sdk.mcp.destructive import (
        is_declared_destructive,
        operator_allows_destructive,
    )

    assert operator_allows_destructive() is False
    monkeypatch.setenv(ALLOW_DESTRUCTIVE_SETTING, "true")
    assert operator_allows_destructive() is True
    assert is_declared_destructive(_Api.suspend_user) is True
    assert is_declared_destructive(_Api.get_user) is False
