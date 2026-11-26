"""Connector tools declare and require an acting user (R902)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from mcp.shared.exceptions import MCPError
from test_actor_binding_served import AUDIENCE, ISSUER, _jwt_server, _serving

from scion_sdk.identity import ActorRequiredError, current_actor
from scion_sdk.mcp.actor_binding import (
    ACTOR_REQUIRED_META,
    ActorBindingMiddleware,
    actor_required,
)
from scion_sdk.mcp.server import create_mcp_server
from scion_sdk.mcp.tool_surface import register_tool_surface

BODY_RUNS: list[str] = []


def _register(mcp: FastMCP[Any]) -> None:
    @mcp.tool(meta={ACTOR_REQUIRED_META: True})
    def flagged() -> str:
        BODY_RUNS.append("flagged")
        return current_actor().actor_id

    @mcp.tool
    def ordinary() -> str:
        return current_actor().actor_id


def _flagged_server(*argv: str) -> FastMCP[Any]:
    _a, mcp, middlewares = create_mcp_server(
        "fixture-actor", version="0.0.0", command_args=list(argv)
    )
    _register(mcp)
    for middleware in middlewares:
        mcp.add_middleware(middleware)
    return mcp


@pytest.fixture(autouse=True)
def _clear_runs() -> None:
    BODY_RUNS.clear()


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.1")
async def test_listing_carries_the_actor_required_meta() -> None:
    mcp = _flagged_server("--transport", "stdio")
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert (tools["flagged"].meta or {}).get(ACTOR_REQUIRED_META) is True
    assert ACTOR_REQUIRED_META not in (tools["ordinary"].meta or {})
    assert actor_required(await mcp.get_tool("flagged"))
    assert not actor_required(await mcp.get_tool("ordinary"))


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.1")
async def test_register_tool_surface_sets_the_meta_on_named_tools() -> None:
    mcp: FastMCP[Any] = FastMCP("surface")

    @mcp.tool
    def lookup() -> str:
        return "x"

    register_tool_surface(mcp, service="svc", actor_required=frozenset({"lookup"}))
    assert actor_required(await mcp.get_tool("lookup"))
    with pytest.raises(ValueError, match="unregistered"):
        register_tool_surface(mcp, service="svc", actor_required=frozenset({"nope"}))


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.2")
async def test_flagged_and_ordinary_tools_resolve_their_actors() -> None:
    keys = RSAKeyPair.generate()
    token = keys.create_token(
        subject="user-7", issuer=ISSUER, audience=AUDIENCE, additional_claims={}
    )
    with _serving(_jwt_server_with_tools(keys)) as endpoint:
        async with Client(endpoint, auth=token) as client:
            assert (await client.call_tool("ordinary", {})).data == "user-7"
            assert (await client.call_tool("flagged", {})).data == "user-7"


def _jwt_server_with_tools(keys: RSAKeyPair) -> FastMCP[Any]:
    mcp = _jwt_server(keys)
    _register(mcp)
    return mcp


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.3")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.6")
async def test_actor_required_tool_binds_forwarded_user() -> None:
    keys = RSAKeyPair.generate()
    good = keys.create_token(subject="user-7", issuer=ISSUER, audience=AUDIENCE)
    wrong = keys.create_token(subject="user-7", issuer=ISSUER, audience="other")
    with _serving(_jwt_server_with_tools(keys)) as endpoint:
        async with Client(endpoint, auth=good) as client:
            assert (await client.call_tool("flagged", {})).data == "user-7"
        assert BODY_RUNS == ["flagged"]
        with pytest.raises(Exception):  # noqa: B017 - transport-level 401
            async with Client(endpoint, auth=wrong) as client:
                await client.call_tool("flagged", {})
    # Local system actor over stdio-equivalent transport is refused.
    async with Client(_flagged_server("--transport", "stdio")) as client:
        with pytest.raises(MCPError):
            await client.call_tool("flagged", {})
    assert BODY_RUNS == ["flagged"]
    # Anonymous HTTP caller is refused.
    anon: FastMCP[Any] = FastMCP("anon")
    _register(anon)
    anon.add_middleware(ActorBindingMiddleware())
    with _serving(anon) as endpoint:
        async with Client(endpoint) as client:
            with pytest.raises(MCPError):
                await client.call_tool("flagged", {})
    assert BODY_RUNS == ["flagged"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.4")
async def test_refusals_run_no_tool_body() -> None:
    anon: FastMCP[Any] = FastMCP("anon")
    _register(anon)
    anon.add_middleware(ActorBindingMiddleware())
    with _serving(anon) as endpoint:
        async with Client(endpoint) as client:
            with pytest.raises(MCPError) as caught:
                await client.call_tool("flagged", {})
    assert caught.value.code == -32000
    assert caught.value.data == {"reason": "actor_required"}
    assert BODY_RUNS == []
    assert issubclass(ActorRequiredError, PermissionError)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R902.5")
async def test_trace_event_names_subject_and_audience_without_token() -> None:
    keys = RSAKeyPair.generate()
    token = keys.create_token(subject="user-7", issuer=ISSUER, audience=AUDIENCE)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("scion_sdk.mcp.actor_binding")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    with _serving(_jwt_server_with_tools(keys)) as endpoint:
        async with Client(endpoint, auth=token) as client:
            await client.call_tool("flagged", {})
    logger.removeHandler(handler)
    (record,) = [r for r in records if r.getMessage() == "actor_required_call"]
    assert record.__dict__["actor_id"] == "user-7"
    assert record.__dict__["audience"] == AUDIENCE
    assert record.__dict__["outcome"] == "allowed"
    assert token not in str(record.__dict__)
