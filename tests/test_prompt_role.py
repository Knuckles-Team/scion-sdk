"""Prompt role and targets survive, as declared, into the engine pack blueprint."""

import json

import pytest
from medulla.generated.connector_pack import PackEntryKind

from scion_sdk.artifacts.pack import build_connector_content_pack
from scion_sdk.mcp.content import ConnectorContent, ContentError


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R074.2")
async def test_prompt_role_and_concepts_reach_the_pack_entry(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for name, declaration in {
        "system": {
            "role": "system",
            "ontology_class": "https://example.org/Item",
            "identity": {"role": "Specialist"},
        },
        "default": {"serves": ["https://example.org/capability"]},
        "template": {"role": "template"},
    }.items():
        declaration["instructions"] = {"core_directive": "Read records."}
        (prompts / f"{name}.json").write_text(json.dumps(declaration))
    pack = await build_connector_content_pack(
        ConnectorContent("demo-agent", tmp_path, "1.0")
    )
    bodies = {}
    for entry in pack.archive.entries:
        if entry.kind is PackEntryKind.PROMPT:
            ref = entry.body
            bodies[entry.name] = json.loads(
                pack.archive.data[ref.offset : ref.offset + ref.length]
            )
    assert bodies["system"]["role"] == "system"
    assert bodies["system"]["ontology_class"] == "https://example.org/Item"
    assert bodies["system"]["identity"] == {"role": "Specialist"}
    assert "role" not in bodies["default"]
    assert bodies["default"]["serves"] == ["https://example.org/capability"]
    assert bodies["template"]["role"] == "template"
    for name, body in bodies.items():
        assert body["task"] == name
        assert body["type"] == "prompt"
        assert body["instructions"] == {"core_directive": "Read records."}


async def test_prompt_role_rejects_unknown_role(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "bad.json").write_text(
        json.dumps({"role": "admin", "instructions": {"core_directive": "Read."}})
    )
    with pytest.raises(ContentError, match="prompt role"):
        await build_connector_content_pack(
            ConnectorContent("demo-agent", tmp_path, "1.0")
        )
