"""Prompt pack entries are canonical blueprints; capture-only prompts are refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from medulla.generated.connector_pack import PackEntryKind

from scion_sdk.artifacts.pack import build_connector_content_pack, build_content_pack
from scion_sdk.artifacts.prompt_blueprint import (
    BLUEPRINT_SCHEMA_VERSION,
    BlueprintViolation,
    blueprint_from_source,
    validate_prompt_blueprint,
)
from scion_sdk.artifacts.prompts import PromptArtifactKind
from scion_sdk.contracts import CapturedArtifact, ServerIdentity
from scion_sdk.manifest.prompt_source import (
    ContentError,
    composed_text,
    prompt_document,
)
from scion_sdk.mcp.content import ConnectorContent
from scion_sdk.ports.errors import MalformedArtifactError
from scion_sdk.testing.results import SessionFactory

SERVER = ServerIdentity(name="demo-mcp", version="1.4.0")
BODY = "Triage the incident."


def _conformant() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task": "incident-triage",
        "type": "prompt",
        "source": "service-desk-connector",
        "instructions": {"core_directive": BODY},
    }


def _entry(document: object, name: str = "incident-triage") -> CapturedArtifact:
    return CapturedArtifact(
        kind="prompt",
        uri=f"prompt://{SERVER.name}/{name}",
        name=name,
        media_type="application/json",
        body=json.dumps(document),
        server=SERVER,
    )


def _bodies(pack) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    found = {}
    for entry in pack.archive.entries:
        if entry.kind is PackEntryKind.PROMPT:
            ref = entry.body
            found[entry.name] = json.loads(
                pack.archive.data[ref.offset : ref.offset + ref.length]
            )
    return found


def _content(tmp_path: Path, files: dict[str, object]) -> ConnectorContent:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name, document in files.items():
        (prompts / f"{name}.json").write_text(json.dumps(document))
    return ConnectorContent("demo-agent", tmp_path, "1.0")


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
async def test_pack_prompt_entry_is_the_declared_blueprint(
    sessions: SessionFactory, package_root: Path
) -> None:
    declared = json.loads((package_root / "prompts" / "demo_agent.json").read_text())
    sources = {"demo_agent": package_root / "prompts" / "demo_agent.json"}
    async with sessions() as session:
        pack = await build_content_pack(
            session, connector="demo-agent", kinds=(PromptArtifactKind(sources),)
        )
    body = _bodies(pack)["demo_agent"]
    assert body == declared
    assert validate_prompt_blueprint(body) == []
    assert "capture" not in body and "result" not in body


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
async def test_envelope_is_completed_from_provenance_never_the_body(
    tmp_path: Path,
) -> None:
    content = _content(
        tmp_path, {"triage": {"instructions": {"core_directive": BODY}, "tags": ["x"]}}
    )
    body = _bodies(await build_connector_content_pack(content))["triage"]
    assert body["schema_version"] == "1.0"
    assert body["task"] == "triage"
    assert body["type"] == "prompt"
    assert body["source"] == "demo-agent/prompts/triage.json"
    assert body["instructions"] == {"core_directive": BODY}
    assert body["tags"] == ["x"]
    assert validate_prompt_blueprint(body) == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
def test_valid_extends_and_compose_pass() -> None:
    document = {**_conformant(), "extends": "prompt://base", "compose": "append"}
    assert validate_prompt_blueprint(document, {"prompt://base"}) == []
    PromptArtifactKind().validate(_entry(_conformant()))


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
def test_each_engine_rule_is_mirrored_with_its_reason() -> None:
    legacy = {
        "task": "Not A Slug",
        "type": "template",
        "content": "secret body text",
        "input": "more",
        "extends": "prompt://missing",
        "compose": "merge",
    }
    found = validate_prompt_blueprint(legacy, {"prompt://base"})
    assert [(v.path, v.rule) for v in found] == [
        ("schema_version", "must_be_1_0"),
        ("task", "must_be_slug"),
        ("type", "must_be_prompt"),
        ("source", "must_name_provenance"),
        ("content", "body_only_in_core_directive"),
        ("input", "body_only_in_core_directive"),
        ("instructions.core_directive", "must_hold_the_body"),
        ("extends", "must_name_an_existing_prompt"),
        ("compose", "must_be_append_prepend_or_replace"),
    ]
    assert all("secret" not in v.path + v.rule for v in found)
    assert [(v.path, v.rule) for v in validate_prompt_blueprint([])] == [
        ("$", "must_be_object")
    ]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
@pytest.mark.parametrize(
    ("key", "value", "path", "rule"),
    [
        ("schema_version", "2.0", "schema_version", "must_be_1_0"),
        ("task", "-bad-", "task", "must_be_slug"),
        ("task", "x" * 129, "task", "must_be_slug"),
        ("task", "Upper", "task", "must_be_slug"),
        ("type", "template", "type", "must_be_prompt"),
        ("source", "  ", "source", "must_name_provenance"),
        (
            "instructions",
            {"core_directive": " "},
            "instructions.core_directive",
            "must_hold_the_body",
        ),
    ],
)
def test_one_broken_field_names_only_its_path_and_rule(
    key: str, value: object, path: str, rule: str
) -> None:
    found = validate_prompt_blueprint({**_conformant(), key: value})
    assert [(v.path, v.rule) for v in found] == [(path, rule)]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R024")
def test_capture_shaped_body_is_refused_at_build_time() -> None:
    capture = {
        "name": "incident-triage",
        "role": "task",
        "description": "",
        "arguments": [],
        "definition": {"name": "incident-triage"},
        "capture": {"method": "prompts/get", "bound_arguments": {}, "kind": "rendered"},
        "result": {"messages": [{"role": "user", "content": {"type": "text"}}]},
    }
    with pytest.raises(MalformedArtifactError) as raised:
        PromptArtifactKind().validate(_entry(capture))
    text = str(raised.value)
    for expected in (
        "schema_version (must_be_1_0)",
        "task (must_be_slug)",
        "type (must_be_prompt)",
        "source (must_name_provenance)",
        "instructions.core_directive (must_hold_the_body)",
    ):
        assert expected in text


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R024")
async def test_served_prompt_without_blueprint_source_names_the_prompt(
    sessions: SessionFactory,
) -> None:
    async with sessions() as session:
        with pytest.raises(MalformedArtifactError, match=r"demo_agent.*no blueprint"):
            await build_content_pack(
                session, connector="demo-agent", kinds=(PromptArtifactKind(),)
            )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R024")
async def test_blueprint_with_top_level_body_is_refused_naming_the_prompt(
    tmp_path: Path,
) -> None:
    content = _content(
        tmp_path,
        {"bad": {"instructions": {"core_directive": BODY}, "content": "dup body"}},
    )
    with pytest.raises(MalformedArtifactError, match=r"'bad'.*content \(body_only"):
        await build_connector_content_pack(content)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R024")
async def test_non_slug_name_without_declared_task_is_refused(tmp_path: Path) -> None:
    content = _content(
        tmp_path, {"Not_Slug": {"instructions": {"core_directive": BODY}}}
    )
    with pytest.raises(
        MalformedArtifactError, match=r"'Not_Slug'.*task \(must_be_slug"
    ):
        await build_connector_content_pack(content)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
def test_one_loader_reads_the_serving_and_the_packing_file(tmp_path: Path) -> None:
    path = tmp_path / "triage.json"
    path.write_text(json.dumps({"instructions": {"core_directive": BODY}}))
    document = prompt_document(path)
    assert composed_text(path, document) == BODY
    built = blueprint_from_source(
        "triage", path, connector="demo-agent", existing=frozenset()
    )
    assert built["schema_version"] == BLUEPRINT_SCHEMA_VERSION
    assert built["instructions"]["core_directive"] == BODY
    path.write_text("[]")
    with pytest.raises(ContentError):
        prompt_document(path)
    with pytest.raises(MalformedArtifactError, match="triage"):
        blueprint_from_source("triage", path, connector="c", existing=frozenset())


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R023")
def test_a_violation_is_a_path_and_a_rule_only() -> None:
    assert BlueprintViolation("task", "must_be_slug") == BlueprintViolation(
        "task", "must_be_slug"
    )
