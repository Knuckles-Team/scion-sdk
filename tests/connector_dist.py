"""Build an installed-looking fixture connector distribution in a temp directory."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

ONTOLOGY = """\
@prefix kg: <http://knuckles.team/kg#> .
@prefix demo: <https://example.invalid/ontology/demo#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

demo:DemoItem a owl:Class ; rdfs:subClassOf kg:Document .
demo:DemoLeaf a owl:Class ; rdfs:subClassOf demo:DemoItem .
"""

ORPHAN = ONTOLOGY + "demo:Lonely a owl:Class .\n"

INIT = """\
from scion_sdk.connector import Connector

from democonn.client import Api, get_client

connector = Connector(api=Api, get_client=get_client, instructions="Demo connector.")
"""

CLIENT = '''\
class Api:
    def __init__(self, base_url: str = "") -> None:
        self.base_url = base_url

    def list_items(self) -> list[dict[str, str]]:
        """List the demo items."""
        return [{"id": "1"}]


def get_client() -> Api:
    return Api()
'''

SOURCES = """\
from scion_sdk.ingest.live_declaration import source_meta

SCHEMA_VERSION = 2
FIELD_SETS = {"common": {"name": "xsd:string", "created": "xsd:dateTime"}}

SOURCES = (
    source_meta(
        tool="demo_list_items",
        source_id="demo-items",
        record_type="DemoItem",
        id_field="id",
    ),
)

SCHEMA_MAPPINGS = {
    "DemoItem": {"ontology_class": "DemoItem", "field_sets": ["common"], "fields": {"size": "xsd:long"}},
    "DemoLeaf": {"ontology_class": "DemoLeaf", "field_sets": ["common"]},
}
RESOURCES = ()
"""

CONTRACTS = """\
from scion_sdk.surface_contracts import (
    BusinessRole,
    DataLocation,
    Refusal,
    SurfaceLink,
    ToolStep,
    ToolUseContract,
)

CONTRACTS = (
    ToolUseContract(
        task="list-demo-items",
        steps=(
            ToolStep(
                tool="demo_list_items",
                result=("id",),
                refusals=(Refusal(code="UNAUTHORIZED", when="the token is rejected"),),
            ),
        ),
    ),
)
LINKS = (
    SurfaceLink(
        task="list-demo-items",
        tools=("demo_list_items",),
        prompts=("demo", "demo-curator"),
        skills=("demo-skill",),
    ),
)
ROLES = (BusinessRole(name="demo curator", tasks=("list-demo-items",)),)
LOCATIONS = (
    DataLocation(
        name="demo-items",
        holds=("DemoItem",),
        instance="demo-default",
        read_tool="demo_list_items",
    ),
)
"""


def _prompt(kind: str, role: str = "") -> str:
    agent = {
        "kind": kind,
        "tools": ["demo_list_items"],
        "skills": ["demo-skill"],
        "data": {"reads": ["demo-items"], "writes": []},
        "guardrails": {"approval_required": [], "never": ["delete items"]},
        "outputs": ["item list"],
    }
    if role:
        agent["business_role"] = role
    return json.dumps(
        {
            "task": "demo",
            "type": "prompt",
            "description": "Demo.",
            "identity": {
                "role": role or "demo operator",
                "goal": "Keep the demo items listed.",
            },
            "instructions": {"core_directive": "Operate the demo."},
            "agent": agent,
        }
    )


PROMPT = _prompt("operator")
ROLE_PROMPT = _prompt("business-role", "demo curator")
SKILL = "---\nname: demo-skill\ndescription: Demo skill.\n---\n# Demo\n"


def build(
    root: Path,
    *,
    version: str = "1.2.3",
    ontology: str = ONTOLOGY,
    init: str = INIT,
    sources: str | None = SOURCES,
    contracts: str | None = CONTRACTS,
    extra_group: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Write package ``democonn`` plus ``democonn-<version>.dist-info`` under ``root``."""
    pkg = root / "democonn"
    for sub in ("skills/demo-skill", "prompts", "ontology"):
        (pkg / sub).mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(init)
    (pkg / "client.py").write_text(CLIENT)
    (pkg / "skills/demo-skill/SKILL.md").write_text(SKILL)
    (pkg / "prompts/demo.json").write_text(PROMPT)
    (pkg / "prompts/demo-curator.json").write_text(ROLE_PROMPT)
    (pkg / "ontology/demo.ttl").write_text(ontology)
    if sources is not None:
        (pkg / "connector_sources.py").write_text(sources)
    if contracts is not None:
        (pkg / "connector_contracts.py").write_text(contracts)
    for name, text in (extra_files or {}).items():
        (pkg / name).write_text(text)
    dist = root / f"democonn-{version}.dist-info"
    dist.mkdir(exist_ok=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: democonn\nVersion: {version}\n"
    )
    (dist / "entry_points.txt").write_text(
        "[scion_sdk.connector]\ndemo-mcp = democonn:connector\n"
        + (f"[{extra_group}]\ndemo-mcp = democonn.skills\n" if extra_group else "")
    )
    extra = (
        f'[project.entry-points."{extra_group}"]\ndemo-mcp = "democonn.skills"'
        if extra_group
        else ""
    )
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [project]
            name = "democonn"
            version = "{version}"

            [project.entry-points."scion_sdk.connector"]
            demo-mcp = "democonn:connector"
            {extra}
            """
        )
    )
    return root
