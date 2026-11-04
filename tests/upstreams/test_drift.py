"""SCION-CONNECTOR-CONTROL-R914.11: schema-fingerprint drift is detected and reported."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from upstream_fixtures import clean_upstream_env, fixture_decl

from scion_sdk.mcp.upstreams import attach_upstreams
from scion_sdk.mcp.upstreams.drift import (
    DriftReport,
    diff_tool_fingerprints,
    fingerprint_tools,
    read_tool_schema,
    tool_fingerprint_map,
)


def test_fingerprint_helpers_report_each_changed_name() -> None:
    tools = [
        SimpleNamespace(
            name="alpha",
            parameters={"type": "object", "properties": {"x": {"type": "int"}}},
        ),
        SimpleNamespace(name="beta", parameters={"type": "object", "properties": {}}),
    ]
    name, input_schema, output_schema = read_tool_schema(tools[0])
    assert name == "alpha"
    assert input_schema["properties"]["x"] == {"type": "int"}
    assert output_schema is None

    mapping = tool_fingerprint_map(tools)
    assert set(mapping) == {"alpha", "beta"}
    # The digest is stable under any listing order.
    assert fingerprint_tools(tools) == fingerprint_tools(list(reversed(tools)))

    report = diff_tool_fingerprints(
        mapping, {**mapping, "alpha": "ff" * 32, "gamma": "ee" * 32}
    )
    assert isinstance(report, DriftReport)
    assert report.is_drifted
    assert "alpha" in report.changed
    assert "gamma" in report.added

    shrunken = {n: fp for n, fp in mapping.items() if n != "beta"}
    assert diff_tool_fingerprints(mapping, shrunken).removed == ("beta",)
    assert diff_tool_fingerprints(None, mapping).names() == ("alpha", "beta")


async def _drift_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: Path,
    on_drift: str,
) -> str:
    """Drive the fixture upstream to drift; returns the pre-drift fingerprint."""
    monkeypatch.setenv("DRIFT_CONTROL_FILE", str(control))
    server = FastMCP(f"drift-{on_drift}-connector")
    decl = fixture_decl(
        "drift",
        data=tmp_path / "data",
        out=tmp_path / "drift-out",
        kind="drift",
        on_drift=on_drift,
        env={"CONTROL_FILE": "env://DRIFT_CONTROL_FILE"},
    )
    registry = attach_upstreams(server, (decl,))
    assert registry is not None

    # One session throughout: the pooled child lives only while a session's
    # lifespan does, so the kill happens inside it.
    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "drift"})
        names = sorted(tool.name for tool in await client.list_tools())
        assert "drift_save" in names and "drift_volatile" in names
        baseline = registry.status("drift")
        assert baseline.state == "ready"
        assert baseline.fingerprint
        pid = int(str((await client.call_tool("drift_whoami", {})).data))

        # The restarted child sees the control file and reports a different
        # schema, so the next list drifts.
        os.kill(pid, signal.SIGKILL)
        await asyncio.sleep(0.5)
        control.touch()
        await client.list_tools()
        status = registry.status("drift")
        report = registry.drift_report("drift")
        assert status.state == "drifted"
        assert "volatile" in report.names()

        if on_drift == "refuse":
            # The drifted namespace lists nothing and its calls refuse.
            names = sorted(tool.name for tool in await client.list_tools())
            assert "drift_save" not in names
            with pytest.raises(ToolError, match="UPSTREAM_DRIFT_REFUSED"):
                await client.call_tool("drift_save", {"what": "x"})
        else:
            # warn keeps serving the namespace while reporting the drift.
            result = await client.call_tool("drift_save", {"what": "x"})
            assert str(result.data) == "saved:x"
    return baseline.fingerprint


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.11")
async def test_fingerprint_mismatch_reports_names_and_refuse_hides_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    monkeypatch.setenv("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "0")
    for directory in (tmp_path / "data", tmp_path / "drift-out"):
        directory.mkdir()

    fingerprint = await _drift_scenario(
        tmp_path, monkeypatch, tmp_path / "control-warn", "warn"
    )

    # A pinned declaration matching the live fingerprint stays ready. The
    # control file is gone, so the live schema is the one the pin was taken on.
    monkeypatch.setenv("DRIFT_CONTROL_FILE", str(tmp_path / "control-none"))
    pinned = fixture_decl(
        "drift",
        data=tmp_path / "data",
        out=tmp_path / "drift-out",
        kind="drift",
        expected_fingerprint=fingerprint,
        env={"CONTROL_FILE": "env://DRIFT_CONTROL_FILE"},
    )
    server = FastMCP("drift-pin-connector")
    registry = attach_upstreams(server, (pinned,))
    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "drift"})
        await client.list_tools()
    assert registry.status("drift").state == "ready"

    # A pin that does not match the live fingerprint is drift.
    mismatched = fixture_decl(
        "drift",
        data=tmp_path / "data",
        out=tmp_path / "drift-out",
        kind="drift",
        expected_fingerprint="ff" * 32,
        env={"CONTROL_FILE": "env://DRIFT_CONTROL_FILE"},
    )
    server = FastMCP("drift-mismatch-connector")
    registry = attach_upstreams(server, (mismatched,))
    async with Client(server, mode="legacy") as client:
        await client.call_tool("registry_activate", {"namespace": "drift"})
        await client.list_tools()
    assert registry.status("drift").state == "drifted"

    await _drift_scenario(tmp_path, monkeypatch, tmp_path / "control-refuse", "refuse")
