"""SCION-CONNECTOR-CONTROL-R914.10: elicitation and confirmation pass through once, with no second gate."""

from __future__ import annotations

from pathlib import Path

import mcp_types
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.elicitation import ElicitResult
from fastmcp.server.providers.proxy import ProxyTool
from upstream_fixtures import clean_upstream_env, fixture_decl

from scion_sdk.mcp.upstreams import attach_upstreams
from scion_sdk.mcp.upstreams.elicitation import (
    ELICITATION_META_KEY,
    ForwardingProxyTool,
)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.10")
async def test_single_prompt_forwarded_and_decline_when_client_cannot_elicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    out.mkdir()
    gamma = fixture_decl("gamma", data=data, out=out, kind="elicit")

    server = FastMCP("elicit-connector")
    attach_upstreams(server, (gamma,))

    # A client that can elicit sees exactly one prompt; the answer reaches the
    # upstream, and the result comes back unchanged.
    prompts: list[tuple[str, object]] = []

    async def handler(
        message: str, response_type: object, params: object, context: object
    ) -> ElicitResult:
        prompts.append((message, response_type))
        return ElicitResult(action="accept", content=True)

    async with Client(server, mode="legacy", elicitation_handler=handler) as client:
        await client.call_tool("registry_activate", {"namespace": "gamma"})
        tools = {tool.name: tool for tool in await client.list_tools()}
        # No confirmation gate is added to the proxied call: the upstream's
        # own parameters pass through unchanged.
        assert list(tools["gamma_ask"].input_schema["properties"]) == ["question"]
        result = await client.call_tool("gamma_ask", {"question": "proceed?"})
    assert str(result.data) == "answer:True"
    assert len(prompts) == 1, "the elicitation is forwarded exactly once"
    assert prompts[0][0] == "proceed?"

    # A client without elicitation gets a decline from the upstream, and the
    # unchanged refusal is tagged with the wire metadata.
    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "gamma"})
        result = await client.call_tool("gamma_ask", {"question": "proceed?"})
    assert str(result.data) == "declined"
    assert (
        result.meta is not None
        and result.meta.get(ELICITATION_META_KEY) == "unavailable"
    )

    # The forwarding proxy tool keeps the upstream tool's identity.
    mcp_tool = mcp_types.Tool(
        name="ask",
        description="Ask.",
        inputSchema={"type": "object", "properties": {"question": {"type": "string"}}},
    )
    proxy = ProxyTool.from_mcp_tool(lambda: None, mcp_tool)
    forwarded = ForwardingProxyTool.from_proxy_tool(proxy)
    assert forwarded.name == "ask"
    assert forwarded.description == "Ask."
