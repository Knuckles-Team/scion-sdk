"""Tool responses and errors redact secrets only, never the cause (SCION-CONNECTOR-CONTROL-R025.7)."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from scion_sdk.mcp.secret_redaction import (
    Redacted,
    SecretBearing,
    SecretBearingDeclarationError,
    secret_bearing_meta,
)
from scion_sdk.mcp.secret_redaction import install as install_secret_redaction
from scion_sdk.privacy import sanitize_tool_response


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7")
def test_a_tool_error_keeps_the_local_path_that_caused_it() -> None:
    error = (
        "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/inv/hosts.yml'"
    )
    payload = {
        "stderr": error,
        "errors": [error, "open " + "/ho" + "me/ops/notes.txt failed", "/var/tmp/x"],
        "details": {"host": "10.0.0.5", "file_path": "/srv/data/a.txt"},
    }
    assert sanitize_tool_response(payload) == payload


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7")
def test_a_tool_response_redacts_secrets() -> None:
    clean = sanitize_tool_response(
        {
            "password": "hunter2",
            "details": {"api_key": "k-123", "token": None},
            "stderr": "auth Bearer abcdefghijklmnop failed for "
            + "postgres"
            + "://u:pw@db/x",
            "stdout": ["token=s3cr3t-value ghp_" + "a" * 24],
        }
    )
    assert clean["password"] == "[REDACTED_SECRET]"
    assert clean["details"] == {"api_key": "[REDACTED_SECRET]", "token": None}
    assert "abcdefghijklmnop" not in clean["stderr"]
    assert ":pw@" not in clean["stderr"] and "postgres://u:" in clean["stderr"]
    assert "s3cr3t-value" not in clean["stdout"][0]
    assert "ghp_" not in clean["stdout"][0]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7.2")
def test_suffix_secret_names_are_redacted() -> None:
    payload = {
        "runners_token": "a",
        "runnersToken": "b",
        "registration_token": "c",
        "webhook_secret": "d",
        "password_value": "e",
        "dsn": "f",
        "session_key": "g",
    }
    assert set(sanitize_tool_response(payload).values()) == {"[REDACTED_SECRET]"}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7.2")
def test_descriptive_flag_and_cursor_names_are_kept() -> None:
    payload = {
        "token_expires_at": "2026-01-01",
        "api_key_env": "SOME_ENV",
        "has_password": True,
        "is_token_set": True,
        "continuation_token": "c1",
        "page_token": "p1",
        "fencing_token": 7,
        "cancellation_token": "x1",
    }
    assert sanitize_tool_response(payload) == payload


MINTED = "minted" + "-secret"


class Minted(BaseModel):
    id: int
    token: SecretBearing[str]
    hidden: Redacted[str] = "plain"


def _server() -> FastMCP:
    server = FastMCP("redaction")

    @server.tool
    def raw_remote() -> dict[str, object]:
        return {"name": "runner", "runners_token": "glrt-secret", "path": "/srv/x"}

    @server.tool(meta=secret_bearing_meta("token"))
    def mint() -> Minted:
        return Minted(id=1, token=MINTED)

    @server.tool
    def read_back() -> dict[str, object]:
        return {"id": 1, "token": MINTED, "note": "token=abc123def456ghi"}

    @server.tool
    def failing() -> str:
        raise RuntimeError("upstream said Authorization: Bearer abc.def.ghijklmnop")

    install_secret_redaction(server)
    return server


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7.1")
async def test_a_served_tool_returning_a_raw_remote_dict_is_redacted() -> None:
    async with Client(_server()) as client:
        result = await client.call_tool("raw_remote")
    assert result.structured_content["runners_token"] == "[REDACTED_SECRET]"
    assert result.structured_content["path"] == "/srv/x"
    assert "glrt-secret" not in str(result.content)
    async with Client(_server()) as client:
        with pytest.raises(ToolError) as caught:
            await client.call_tool("failing")
    assert "abc.def.ghijklmnop" not in str(caught.value)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7.3")
async def test_a_minted_secret_is_returned_once_and_marked() -> None:
    server = _server()
    async with Client(server) as client:
        minted = await client.call_tool("mint")
        other = await client.call_tool("read_back")
        tools = {tool.name: tool for tool in await client.list_tools()}
    assert minted.structured_content["token"] == "minted-secret"
    assert minted.structured_content["hidden"] == "[REDACTED_SECRET]"
    assert other.structured_content["token"] != "minted-secret"
    schema = tools["mint"].outputSchema["properties"]
    assert schema["token"]["x-secret-bearing"] is True
    assert schema["hidden"]["x-redacted"] is True
    assert tools["mint"].meta["returnsSecret"] is True
    assert tools["mint"].meta["secretFields"] == ["token"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7.3")
def test_a_secret_bearing_name_outside_the_output_schema_fails_the_build() -> None:
    server = FastMCP("bad")

    @server.tool(meta=secret_bearing_meta("nope"))
    def mint() -> dict[str, str]:
        return {"value": "x"}

    with pytest.raises(SecretBearingDeclarationError) as caught:
        install_secret_redaction(server)
    assert caught.value.tool == "mint"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R025.7.4")
async def test_trace_and_log_records_of_a_tool_call_hold_no_secret_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="scion_sdk.mcp.tool_call")
    async with Client(_server()) as client:
        await client.call_tool("mint")
        with pytest.raises(ToolError):
            await client.call_tool("failing")
    recorded = " ".join(
        str(r.__dict__) for r in caplog.records if r.name == "scion_sdk.mcp.tool_call"
    )
    assert "tool_call" in recorded
    assert "minted-secret" not in recorded and "abc.def.ghijklmnop" not in recorded


def test_secret_field_rule_and_stamping_unit() -> None:
    from scion_sdk.mcp.secret_redaction import (
        SecretRedactionMiddleware,
        stamp_secret_bearing,
    )
    from scion_sdk.privacy_rules import is_secret_field

    assert is_secret_field("runners_token") and not is_secret_field("page_token")
    stamp_secret_bearing([])
    assert SecretRedactionMiddleware is not None
