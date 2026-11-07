"""SCION-CONNECTOR-CONTROL-R914.12: certification treats upstream tools as declared and unpinned."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from upstream_fixtures import clean_upstream_env, fixture_decl, startup_marker

from scion_sdk.certify.certification import certify_connector
from scion_sdk.certify.listing import (
    ToolListingError,
    UpstreamReport,
    split_upstream_tools,
    upstream_reports,
)
from scion_sdk.ingest.live_declaration import source_meta
from scion_sdk.mcp.declaration_resource import register_live_declaration
from scion_sdk.mcp.upstreams import attach_upstreams
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.transports.mcp import McpTransport
from scion_sdk.upstream_wire import UPSTREAM_META_KEY


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R914.12")
async def test_certify_excludes_proxied_tools_from_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean_upstream_env(monkeypatch)
    data, out_a, out_b = tmp_path / "data", tmp_path / "out-a", tmp_path / "out-b"
    for directory in (data, out_a, out_b):
        directory.mkdir()
    alpha = fixture_decl("alpha", data=data, out=out_a, expected_fingerprint="fp-alpha")
    beta = fixture_decl("beta", data=data, out=out_b, expected_fingerprint="fp-beta")

    server = FastMCP("demo-mcp", version="1.0.0")

    preset_meta = source_meta(
        source_id="demo",
        record_type="Item",
        id_field="id",
        records_path="items",
        preset={
            "pagination": "cursor",
            "cursor_param": "cursor",
            "cursor_path": "cursor",
            "params_style": "args",
        },
    )

    @server.tool(meta=preset_meta)
    def demo_reader(cursor: str = ""):
        """Read a demo item."""
        return {"items": [{"id": "1"}], "cursor": cursor}

    attach_upstreams(server, (alpha, beta))
    register_live_declaration(server, "demo-mcp")

    report = await certify_connector(
        McpTransport(), TransportEndpoint(in_process=server), timeout_seconds=30
    )
    assert report.listed and report.passed
    # Local tools are pinned exactly as before; proxied tools never are.
    assert [verdict.tool for verdict in report.verdicts] == ["demo_reader"]

    # Both upstreams are named, with their declared expectations.
    assert [upstream.name for upstream in report.upstreams] == ["alpha", "beta"]
    assert all(isinstance(upstream, UpstreamReport) for upstream in report.upstreams)
    assert all(upstream.state == "inactive" for upstream in report.upstreams)
    assert report.upstreams[0].expected_fingerprint == "fp-alpha"
    assert report.upstreams[1].fingerprint is None  # never listed while inactive
    document = report.document()
    assert [entry["name"] for entry in document["upstreams"]] == ["alpha", "beta"]

    # Certification never activates: no upstream process started.
    assert not startup_marker(out_a, "tools").exists()
    assert not startup_marker(out_b, "tools").exists()

    # The split is keyed only on the wire metadata, and the wire body parses.
    local, proxied = split_upstream_tools(
        [
            SimpleNamespace(name="local", meta=None),
            SimpleNamespace(
                name="alpha_save",
                meta={UPSTREAM_META_KEY: {"namespace": "alpha", "upstream": "alpha"}},
            ),
        ]
    )
    assert [tool.name for tool in local] == ["local"]
    assert [tool.name for tool in proxied] == ["alpha_save"]

    reports = upstream_reports(
        json.dumps(
            {
                "upstreams": [
                    {
                        "name": "alpha",
                        "namespace": "alpha",
                        "kind": "stdio",
                        "state": "inactive",
                        "tool_count": 0,
                    }
                ]
            }
        )
    )
    assert reports[0].name == "alpha" and reports[0].state == "inactive"
    assert upstream_reports("") == ()
    with pytest.raises(ToolListingError):
        upstream_reports("not json")
    with pytest.raises(ToolListingError):
        upstream_reports(json.dumps({"upstreams": {"name": "alpha"}}))
