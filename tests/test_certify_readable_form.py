"""Certification refuses unscored readable-form declarations on live tools."""

import json

import pytest
from fastmcp import FastMCP
from mcp_types import ToolAnnotations

from scion_sdk.artifacts.tools import ToolArtifactKind
from scion_sdk.certify.certification import certify_connector
from scion_sdk.certify.read_only_check import ToolFixture
from scion_sdk.certify.verdicts import ToolStatus
from scion_sdk.ingest.live_declaration import (
    source_meta,
)
from scion_sdk.manifest.live_contract import validate_live_tool_contract
from scion_sdk.mcp.declaration_resource import register_live_declaration
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.transports.mcp import McpTransport


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R048.4")
@pytest.mark.parametrize("declared_on_preset", [True, False])
@pytest.mark.parametrize("question_set_ref", [None, "questions.json"])
async def test_unscored_form_is_refused(
    declared_on_preset: bool, question_set_ref: str | None
) -> None:
    server = FastMCP("demo-mcp")
    form = {"template_id": "default", "question_set_ref": question_set_ref}
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
    if declared_on_preset:
        preset_meta["eg.annotations"] = {"readable_form": form}

    @server.tool(meta=preset_meta)
    def read_items(cursor: str = ""):
        return {"items": [], "cursor": cursor}

    if not declared_on_preset:

        @server.tool(meta={"eg.annotations": {"readable_form": form}})
        def analysis():
            return {"terms": []}

    register_live_declaration(server, "demo-mcp")
    report = await certify_connector(
        McpTransport(), TransportEndpoint(in_process=server), timeout_seconds=10
    )
    assert not report.passed
    rejected = [
        verdict
        for verdict in report.verdicts
        if verdict.status is ToolStatus.UNSCORED_READABLE_FORM
    ]
    assert len(rejected) == 1
    assert rejected[0].tool == ("read_items" if declared_on_preset else "analysis")
    assert any(
        tool["status"] == "unscored_readable_form"
        for tool in report.document()["tools"]
    )


def _graded_server(answers: list[bool] | None) -> FastMCP:
    server = FastMCP("demo")
    form: dict[str, object] = {"template_id": "default", "question_set_ref": "q.json"}
    if answers is not None:
        form["graded_answers"] = answers
    meta = source_meta(
        source_id="items",
        record_type="Item",
        id_field="id",
        preset={"params_style": "args"},
    )
    meta["eg.annotations"] = {"readable_form": form}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True), meta=meta)
    def read_items() -> dict:
        return {"items": []}

    register_live_declaration(server, "demo")
    return server


async def _certify(server: FastMCP) -> object:
    transport = McpTransport()
    endpoint = TransportEndpoint(in_process=server)
    async with transport.session(endpoint) as session:
        pin = validate_live_tool_contract(
            await session.list_tools(), tool_name="read_items"
        ).compatibility_sha256
    fixture = ToolFixture(
        tool="read_items", fingerprint=pin, arguments={}, observed_effects=frozenset()
    )
    return await certify_connector(
        transport, endpoint, timeout_seconds=10, fixtures={"read_items": fixture}
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R048.5")
async def test_verdict_records_question_counts() -> None:
    report = await _certify(_graded_server([True] * 7 + [False] * 3))
    (verdict,) = report.verdicts  # type: ignore[attr-defined]
    assert verdict.status is ToolStatus.CERTIFIED
    assert (verdict.question_count, verdict.answered_count) == (10, 7)
    assert verdict.score == 0.7
    unscored = await _certify(_graded_server(None))
    assert unscored.verdicts[0].status is ToolStatus.UNSCORED_READABLE_FORM  # type: ignore[attr-defined]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R048")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R048.6")
async def test_certify_run_scores_form_and_pack_carries_it() -> None:
    server = _graded_server([True] * 9 + [False])
    report = await _certify(server)
    assert report.passed  # type: ignore[attr-defined]
    document = report.document()["tools"][0]  # type: ignore[attr-defined]
    assert document["score"] == 0.9 and document["question_count"] == 10
    async with McpTransport().session(TransportEndpoint(in_process=server)) as session:
        identity = await session.server_identity()
        (entry,) = await ToolArtifactKind().list_entries(session, identity)
    assert json.loads(entry.body)["readable_form"]["template_id"] == "default"
