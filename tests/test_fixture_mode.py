"""Explicit fixture serving mode (SCION-CONNECTOR-CONTROL-R931)."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from scion_sdk.mcp.content import ConnectorContent
from scion_sdk.mcp.fixture_mode import (
    FIXTURE_DIRECTORY,
    FixtureMiddleware,
    FixtureNotFound,
    FixtureStore,
    argument_digest,
)
from scion_sdk.mcp.server import create_mcp_server

_RECORDED = {"city": "Lyon"}


def _package(tmp_path: Path) -> ConnectorContent:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "weather.json").write_text(
        json.dumps(
            [
                {
                    "tool": "weather",
                    "arguments": _RECORDED,
                    "result": {"text": "sunny", "structured": {"result": "sunny"}},
                }
            ]
        ),
        encoding="utf-8",
    )
    return ConnectorContent(
        connector="demo", package_root=tmp_path, package_version="1.0.0"
    )


def _server(content: ConnectorContent, args: list[str]) -> Any:
    _args, mcp, middlewares = create_mcp_server(
        "demo", version="1", command_args=args, content=content
    )
    calls: list[dict[str, Any]] = []

    @mcp.tool
    def weather(city: str) -> str:
        """A live tool: reading a credential or the network here is a defect."""
        calls.append({"city": city})
        raise AssertionError("the live tool body must never run in fixture mode")

    for item in middlewares:
        mcp.add_middleware(item)
    return mcp, calls


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R931")
def test_fixture_mode_serves_recorded_answers_and_refuses_unrecorded_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("CONNECTOR_TOKEN", "API_KEY", "SCION_FIXTURE_MODE"):
        monkeypatch.delenv(name, raising=False)
    content = _package(tmp_path)
    mcp, calls = _server(content, ["--fixture-mode"])

    def _no_sockets(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("fixture mode opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", _no_sockets)
    monkeypatch.setattr(socket, "create_connection", _no_sockets)

    async def scenario() -> None:
        async with Client(mcp) as client:
            # The full declared surface is listed.
            assert "weather" in {t.name for t in await client.list_tools()}
            # A recorded call answers from the fixture, flagged as one.
            ok = await client.call_tool("weather", _RECORDED)
            assert ok.content[0].text == "sunny"  # type: ignore[union-attr]
            assert ok.structured_content == {"result": "sunny"}
            assert ok.meta is not None and ok.meta["fixture_mode"] is True
            # An unrecorded call is refused with the typed code; nothing is invented.
            with pytest.raises(Exception, match="fixture_not_found"):
                await client.call_tool("weather", {"city": "Oslo"})

    asyncio.run(scenario())
    assert calls == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R931")
def test_fixture_mode_is_explicit_and_never_implied(tmp_path: Path) -> None:
    content = _package(tmp_path)
    args, mcp, middlewares = create_mcp_server(
        "demo", version="1", command_args=[], content=content
    )
    assert args.fixture_mode is False
    assert not any(isinstance(m, FixtureMiddleware) for m in middlewares)
    # Without the flag the live tool runs: a fixture is never read.
    ran: list[str] = []

    @mcp.tool
    def weather(city: str) -> str:
        """Live."""
        ran.append(city)
        return "live"

    async def scenario() -> None:
        async with Client(mcp) as client:
            result = await client.call_tool("weather", _RECORDED)
            assert result.content[0].text == "live"  # type: ignore[union-attr]

    asyncio.run(scenario())
    assert ran == ["Lyon"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R931")
def test_the_single_setting_selects_the_mode_and_a_bare_package_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCION_FIXTURE_MODE", "true")
    args, _mcp, middlewares = create_mcp_server(
        "demo", version="1", command_args=[], content=_package(tmp_path)
    )
    assert args.fixture_mode is True
    assert any(isinstance(m, FixtureMiddleware) for m in middlewares)
    # A connector with no content root or no fixtures refuses to start.
    with pytest.raises(SystemExit):
        create_mcp_server("demo", version="1", command_args=[])
    bare = tmp_path / "bare"
    (bare / "fixtures").mkdir(parents=True)
    with pytest.raises(SystemExit):
        create_mcp_server(
            "demo",
            version="1",
            command_args=[],
            content=ConnectorContent(
                connector="demo", package_root=bare, package_version="1"
            ),
        )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R931")
def test_fixtures_are_keyed_by_the_canonical_argument_digest(tmp_path: Path) -> None:
    assert argument_digest({"b": 1, "a": 2}) == argument_digest({"a": 2, "b": 1})
    assert argument_digest(None) == argument_digest({})
    store = FixtureStore.load(_package(tmp_path).package_root / "fixtures")
    assert len(store) == 1
    (tmp_path / "fixtures" / "bad.json").write_text('{"not": "a list"}')
    with pytest.raises(ValueError, match="list of entries"):
        FixtureStore.load(tmp_path / "fixtures")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R931")
def test_an_unrecorded_call_is_the_typed_refusal(tmp_path: Path) -> None:
    store = FixtureStore.load(_package(tmp_path).package_root / FIXTURE_DIRECTORY)
    with pytest.raises(FixtureNotFound) as refused:
        store.answer("weather", {"city": "Oslo"})
    assert refused.value.reason == "fixture_not_found"
    assert refused.value.code == -32000
