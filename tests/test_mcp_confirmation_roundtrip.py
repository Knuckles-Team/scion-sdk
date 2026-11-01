"""Served MCP confirmation round trips for both SDK dispatch paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastmcp import Context, FastMCP
from fastmcp.client import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from scion_sdk.mcp.action_dispatch import dispatch_async
from scion_sdk.mcp.confirmation import (
    confirmation_refusal as confirmation_refusal_direct,
)
from scion_sdk.mcp.confirmation import (
    elicitation_unavailable as elicitation_unavailable_direct,
)
from scion_sdk.mcp.context import (
    confirmation_refusal,
    ctx_confirm_destructive,
    elicitation_unavailable,
)
from scion_sdk.mcp.method_tools import register_method_tools


class _Api:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def delete_item(self, item_id: str) -> int:
        """Delete an item through the controlled provider transport."""
        response = await self._client.delete(f"https://provider.test/items/{item_id}")
        return response.status_code

    async def get_item(self, item_id: str) -> int:
        """Read an item through the controlled provider transport."""
        response = await self._client.get(f"https://provider.test/items/{item_id}")
        return response.status_code


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
@pytest.mark.parametrize("path", ["generated_method", "action_dispatch"])
@pytest.mark.parametrize(
    ("mode", "protocol"),
    [("auto", "2026-07-28"), ("legacy", "2025-11-25")],
)
@pytest.mark.parametrize(
    ("decision", "expected_effects"),
    [
        ("accept", 1),
        ("false", 0),
        ("truthy_object_false", 0),
        ("non_boolean", 0),
        ("decline", 0),
        ("cancel", 0),
        ("missing", 0),
        ("error", 0),
    ],
)
async def test_served_destructive_confirmation_never_bypasses_gate(
    path: str, mode: str, protocol: str, decision: str, expected_effects: int
) -> None:
    observed_effects: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        observed_effects.append(request.url.path)
        return httpx.Response(204)

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as provider:
        api = _Api(provider)
        server = FastMCP("confirmation-regression")
        if path == "generated_method":
            assert register_method_tools(
                server, _Api, lambda: api, prefix="provider"
            ) == [
                "provider_delete_item",
                "provider_get_item",
            ]
            tool_name = "provider_delete_item"
        else:

            @server.tool(name="provider_delete_item")
            async def delete_item(item_id: str, ctx: Context) -> Any:
                return await dispatch_async(
                    api, "delete_item", {"item_id": item_id}, ctx=ctx
                )

            tool_name = "provider_delete_item"

        observed_protocols: list[str] = []
        elicitation_requests: list[str] = []

        @server.tool(name="observed_protocol")
        async def observed_protocol(ctx: Context) -> str:
            assert ctx.request_context is not None
            observed_protocols.append(ctx.request_context.protocol_version)
            return ctx.request_context.protocol_version

        async def elicit(
            message: str,
            response_type: type[Any] | None,
            params: Any,
            context: Any,
        ) -> Any:
            elicitation_requests.append(message)
            if decision == "error":
                raise RuntimeError("elicitation transport failed")
            if decision == "decline":
                return ElicitResult(action="decline")
            if decision == "cancel":
                return ElicitResult(action="cancel")
            if decision == "missing":
                return ElicitResult(action="accept", content=None)
            if decision == "accept":
                return {"confirmed": True}
            if decision in {"false", "truthy_object_false"}:
                return {"confirmed": False}
            if decision == "non_boolean":
                return {"confirmed": "yes"}
            raise AssertionError(f"unexpected decision: {decision}")

        async with Client(server, mode=mode, elicitation_handler=elicit) as mcp_client:
            await mcp_client.call_tool("observed_protocol", {})
            tool_arguments = {"item_id": "x"}
            if decision == "error" and mode == "auto":
                with pytest.raises(MCPError, match="elicitation transport failed"):
                    await mcp_client.call_tool(tool_name, tool_arguments)
            else:
                await mcp_client.call_tool(tool_name, tool_arguments)

    assert observed_protocols == [protocol]
    assert len(elicitation_requests) == 1
    assert len(observed_effects) == expected_effects
    if expected_effects:
        assert observed_effects == ["/items/x"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
@pytest.mark.parametrize("path", ["generated_method", "action_dispatch"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_nondestructive_served_call_does_not_request_confirmation(
    path: str, mode: str
) -> None:
    observed: list[str] = []
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        observed.append(f"{request.method} {request.url.path}")
        return httpx.Response(200)

    async def elicit(
        message: str, response_type: type[Any] | None, params: Any, context: Any
    ) -> Any:
        requests.append(message)
        return ElicitResult(action="accept", content={"confirmed": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as provider:
        api = _Api(provider)
        server = FastMCP("nondestructive-confirmation-regression")
        if path == "generated_method":
            register_method_tools(server, _Api, lambda: api, prefix="provider")
            tool_name = "provider_get_item"
            arguments = {"item_id": "x"}
        else:

            @server.tool(name="provider_get_item")
            async def get_item(item_id: str, ctx: Context) -> int:
                return await dispatch_async(
                    api, "get_item", {"item_id": item_id}, ctx=ctx
                )

            tool_name = "provider_get_item"
            arguments = {"item_id": "x"}

        async with Client(server, mode=mode, elicitation_handler=elicit) as mcp_client:
            await mcp_client.call_tool(tool_name, arguments)

    assert observed == ["GET /items/x"]
    assert requests == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
@pytest.mark.parametrize("missing_context", [True, False])
async def test_missing_or_unversioned_context_denies_before_provider_effect(
    missing_context: bool,
) -> None:
    observed: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as provider:
        api = _Api(provider)
        context = None if missing_context else SimpleNamespace(request_context=None)
        result = await dispatch_async(api, "delete_item", {"item_id": "x"}, ctx=context)

    assert result == {"cancelled": True, "operation": "delete_item"}
    assert observed == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
async def test_legacy_boolean_guard_fails_closed_on_pending_modern_confirmation() -> (
    None
):
    effects: list[str] = []
    elicitation_requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        effects.append(request.url.path)
        return httpx.Response(204)

    async def elicit(
        message: str, response_type: type[Any] | None, params: Any, context: Any
    ) -> Any:
        elicitation_requests.append(message)
        return {"confirmed": True}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as provider:
        api = _Api(provider)
        server = FastMCP("legacy-boolean-guard-regression")

        @server.tool(name="legacy_generated_delete")
        async def old_generated_tool(ctx: Context) -> int:
            # Exact guard shape from existing OpenApiTool generated consumers.
            if not await ctx_confirm_destructive(ctx, "delete item"):
                raise PermissionError("confirmation required")
            return await api.delete_item("x")

        async with Client(
            server, mode="auto", elicitation_handler=elicit
        ) as mcp_client:
            with pytest.raises(ToolError, match="confirmation required"):
                await mcp_client.call_tool("legacy_generated_delete", {})

    assert elicitation_requests == []
    assert effects == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
@pytest.mark.parametrize("path", ["generated_method", "action_dispatch"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("confirm", [False, True])
async def test_client_without_elicitation_refuses_cleanly_or_confirms_explicitly(
    path: str, mode: str, confirm: bool
) -> None:
    """No elicitation capability: never a protocol failure, never a silent skip."""
    observed: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as provider:
        api = _Api(provider)
        server = FastMCP("no-elicitation-regression")
        if path == "generated_method":
            register_method_tools(server, _Api, lambda: api, prefix="provider")
        else:

            @server.tool(name="provider_delete_item")
            async def delete_item(
                item_id: str, ctx: Context, confirm: bool = False
            ) -> Any:
                return await dispatch_async(
                    api, "delete_item", {"item_id": item_id}, ctx=ctx, confirm=confirm
                )

        async with Client(server, mode=mode) as mcp_client:
            arguments: dict[str, Any] = {"item_id": "x"}
            if confirm:
                arguments["confirm"] = True
            result = await mcp_client.call_tool("provider_delete_item", arguments)

    if confirm:
        assert observed == ["/items/x"]
    else:
        assert observed == []
        assert result.data["cancelled"] is True
        assert "confirm=true" in result.data["reason"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
async def test_only_destructive_method_tools_carry_the_confirm_parameter() -> None:
    server = FastMCP("confirm-parameter-surface")
    register_method_tools(
        server, _Api, lambda: _Api(httpx.AsyncClient()), prefix="provider"
    )
    async with Client(server) as mcp_client:
        tools = {tool.name: tool for tool in await mcp_client.list_tools()}
    assert "confirm" in tools["provider_delete_item"].input_schema["properties"]
    assert "confirm" not in tools["provider_get_item"].input_schema["properties"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R026.1")
@pytest.mark.parametrize("elicits", [False, True])
async def test_elicitation_unavailable_reads_the_live_client_capability(
    elicits: bool,
) -> None:
    server = FastMCP("capability-probe")
    seen: list[tuple[bool, dict[str, Any]]] = []

    @server.tool(name="probe")
    async def probe(ctx: Context) -> str:
        assert elicitation_unavailable_direct(ctx) == elicitation_unavailable(ctx)
        seen.append((elicitation_unavailable(ctx), confirmation_refusal(ctx, "x")))
        assert confirmation_refusal_direct(ctx, "x") == confirmation_refusal(ctx, "x")
        return "ok"

    async def elicit(*args: Any) -> Any:
        return {"confirmed": True}

    kwargs: dict[str, Any] = {"elicitation_handler": elicit} if elicits else {}
    async with Client(server, **kwargs) as mcp_client:
        await mcp_client.call_tool("probe", {})

    unavailable, refusal = seen[0]
    assert unavailable is (not elicits)
    assert ("reason" in refusal) is (not elicits)
    assert refusal["cancelled"] is True and refusal["operation"] == "x"
    assert elicitation_unavailable(None) is False
