"""SCION-CONNECTOR-CONTROL-R916: connector surface contracts and self-check."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from connector_dist import CONTRACTS, build

from scion_sdk.surface_contracts import (
    CONTRACT_NS,
    CORE_NS,
    BusinessRole,
    DataLocation,
    Refusal,
    SurfaceDeclaration,
    SurfaceLink,
    ToolStep,
    ToolUseContract,
    complete,
    complete_prompts,
    load_surface,
    main,
    prompt_agent_findings,
    render_surface_turtle,
    surface_contract_vocabulary,
    surface_findings,
)
from scion_sdk.testing.ontology_rollup import orphan_classes, parse_triples
from scion_sdk.testing.surface_check import main as check_main
from scion_sdk.testing.surface_check import surface_check

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def _drop_demo() -> None:
    for name in [m for m in sys.modules if m.split(".")[0] == "democonn"]:
        sys.modules.pop(name)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    def install(**kwargs):
        _drop_demo()
        build(tmp_path, **kwargs)
        monkeypatch.syspath_prepend(str(tmp_path))
        return tmp_path

    yield install
    _drop_demo()


def _declaration() -> SurfaceDeclaration:
    return SurfaceDeclaration(
        connector="demo-mcp",
        contracts=[
            ToolUseContract(
                task="publish",
                steps=[
                    ToolStep(
                        "demo_get",
                        ["id"],
                        ["id", "title"],
                        [Refusal("NOT_FOUND", "no item")],
                    ),
                    ToolStep("demo_put", ["id", "body"], ["id"]),
                ],
            )
        ],
        links=[
            SurfaceLink(
                "publish",
                tools=["demo_get", "demo_put"],
                prompts=["op"],
                skills=["pub"],
            )
        ],
        locations=[
            DataLocation(
                "items",
                ["DemoItem"],
                "primary",
                "demo_get",
                store="db",
                route_param="site",
            )
        ],
        roles=[BusinessRole("publisher", tasks=["publish"])],
    )


_SERVED = {
    "tools": {"demo_get", "demo_put"},
    "prompts": {"op", "connector-status"},
    "skills": {"pub"},
    "resources": {
        "connector://resources",
        "ontology://demo-mcp/demo.ttl",
        "skill://pub/SKILL.md",
    },
}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.1")
def test_vocabulary_rolls_up_to_core_and_never_imports_it(tmp_path) -> None:
    text = surface_contract_vocabulary()
    path = tmp_path / "v.ttl"
    path.write_text(text)
    assert orphan_classes(path) == set()
    triples = parse_triples(text)
    declared = {s for s, p, o in triples if p == _RDF_TYPE and o.endswith("#Class")}
    assert {
        CONTRACT_NS + n
        for n in (
            "ToolUseContract",
            "ToolStep",
            "Refusal",
            "DataLocation",
            "BusinessRole",
        )
    } <= declared
    assert not [t for t in triples if t[1].endswith("#imports")]
    assert (
        CONTRACT_NS + "BusinessRole",
        "http://www.w3.org/2000/01/rdf-schema#subClassOf",
        CORE_NS + "Role",
    ) in triples


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.2")
def test_builders_normalise_lists_and_render_deterministic_individuals() -> None:
    decl = _declaration()
    assert decl.contracts[0].steps[0].params == ("id",)
    assert decl.locations[0].holds == ("DemoItem",)
    text = render_surface_turtle(decl)
    assert text == render_surface_turtle(_declaration())
    assert "owl:imports" not in text
    triples = set(parse_triples(text))
    contract = "urn:scion:demo-mcp:contract:publish"
    step1, step2 = (
        "urn:scion:demo-mcp:step:publish.1",
        "urn:scion:demo-mcp:step:publish.2",
    )
    assert (contract, _RDF_TYPE, CONTRACT_NS + "ToolUseContract") in triples
    assert (contract, CORE_NS + "hasStep", step1) in triples
    assert (step1, CORE_NS + "precedes", step2) in triples
    assert (
        step1,
        CONTRACT_NS + "callsTool",
        "urn:scion:demo-mcp:tool:demo_get",
    ) in triples
    assert ("urn:scion:demo-mcp:tool:demo_get", _RDF_TYPE, CORE_NS + "Tool") in triples
    assert (
        "urn:scion:demo-mcp:prompt:op",
        CONTRACT_NS + "usesTool",
        "urn:scion:demo-mcp:tool:demo_put",
    ) in triples
    assert (
        "urn:scion:demo-mcp:skill:pub",
        CONTRACT_NS + "servesTask",
        "urn:scion:demo-mcp:task:publish",
    ) in triples
    location = "urn:scion:demo-mcp:location:items"
    assert (
        location,
        CONTRACT_NS + "holdsClass",
        "urn:scion:demo-mcp:class:DemoItem",
    ) in triples
    assert (
        location,
        CONTRACT_NS + "readTool",
        "urn:scion:demo-mcp:tool:demo_get",
    ) in triples
    assert (
        "urn:scion:demo-mcp:role:publisher",
        _RDF_TYPE,
        CONTRACT_NS + "BusinessRole",
    ) in triples


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.2")
def test_load_surface_reads_the_package_module(demo) -> None:
    root = demo()
    decl = load_surface("democonn")
    assert decl is not None and decl.connector == "demo-mcp"
    assert decl.contracts[0].steps[0].tool == "demo_list_items"
    assert decl.roles[0].name == "demo curator"
    _drop_demo()
    (root / "democonn/connector_contracts.py").unlink()
    assert load_surface("democonn") is None


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.4")
def test_a_complete_surface_has_no_findings() -> None:
    assert surface_findings(_declaration(), **_SERVED) == []


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.4")
def test_each_missing_element_is_a_named_finding() -> None:
    found = surface_findings(
        None,
        tools=set(),
        prompts={"connector-status"},
        skills=set(),
        resources={"skill://x/SKILL.md"},
    )
    for gap in (
        "no tools served",
        "no data resource served",
        "no prompt served",
        "no skill served",
        "no ontology resource served",
        "no connector_contracts declaration",
    ):
        assert gap in found
    routed = surface_findings(
        _declaration(),
        **_SERVED,
        tool_schemas={"demo_get": {"properties": {"action": {}}}},
    )
    assert routed == ["tool demo_get routes an action parameter (condensed tool)"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.4")
def test_contract_link_and_location_gaps_are_named() -> None:
    decl = SurfaceDeclaration(
        connector="demo-mcp",
        contracts=[ToolUseContract("t", [ToolStep("ghost")])],
        links=[SurfaceLink("t", tools=["demo_get"], prompts=["missing"])],
        locations=[DataLocation("l", [], "", "ghost")],
    )
    found = surface_findings(decl, **_SERVED)
    for gap in (
        "contract t step 1 names unserved tool ghost",
        "contract t step 1 has no result fields",
        "no refusal declared in any contract",
        "tool demo_put has no contract step",
        "tool demo_put is named by no prompt or skill link",
        "link t names unserved prompt missing",
        "prompt op is not linked to a task",
        "skill pub is not linked to a task",
        "location l reads through unserved tool ghost",
        "location l holds no class",
        "location l names no instance",
    ):
        assert gap in found
    assert surface_findings(SurfaceDeclaration("demo-mcp"), **_SERVED)[-3:] != []
    assert "no data location declared" in surface_findings(
        SurfaceDeclaration("demo-mcp"), **_SERVED
    )


def _agent_document(**agent) -> dict:
    base = {
        "kind": "operator",
        "tools": ["demo_get"],
        "skills": ["pub"],
        "data": {"reads": ["items"], "writes": []},
        "guardrails": {"approval_required": [], "never": ["leak secrets"]},
        "outputs": ["report"],
    }
    base.update(agent)
    return {"identity": {"role": "operator", "goal": "publish"}, "agent": base}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.8")
def test_prompt_agent_definitions_are_validated() -> None:
    decl = _declaration()
    assert (
        prompt_agent_findings(
            _agent_document(), decl, {"demo_get", "demo_put"}, {"pub"}
        )
        == []
    )
    found = prompt_agent_findings(
        _agent_document(
            kind="business-role",
            tools=["demo_x"],
            skills=["nope"],
            data={},
            guardrails={},
            outputs=[],
        ),
        decl,
        {"demo_get"},
        {"pub"},
        name="p",
    )
    for gap in (
        "prompt p is a business-role agent with no business_role",
        "prompt p names unserved tool demo_x",
        "prompt p names tool demo_x that no contract step uses",
        "prompt p names unpackaged skill nope",
        "prompt p reads and writes no data location",
        "prompt p has no never guardrail",
        "prompt p names no output",
    ):
        assert gap in found
    assert prompt_agent_findings({}, decl, set(), set(), name="p") == [
        "prompt p has no identity.role",
        "prompt p has no identity.goal",
        "prompt p has no agent definition",
    ]
    roles = surface_findings(
        decl, **_SERVED, prompt_documents={"op": _agent_document()}
    )
    assert "business role publisher has no business-role prompt" in roles
    role_doc = _agent_document(kind="business-role", business_role="publisher")
    assert "no operator prompt" in surface_findings(
        decl, **_SERVED, prompt_documents={"op": role_doc}
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.6")
def test_served_contracts_resource_carries_the_declaration(demo) -> None:
    from fastmcp import Client

    from scion_sdk.connector import build_server

    demo()
    mcp = build_server("democonn", command_args=[])[1]

    async def read() -> tuple[set[str], str, dict]:
        async with Client(mcp) as client:
            uris = {str(r.uri) for r in await client.list_resources()}
            text = (await client.read_resource("ontology://demo-mcp/contracts.ttl"))[
                0
            ].text
            prompts = {p.name: p.meta for p in await client.list_prompts()}
            return uris, text, prompts

    uris, text, prompts = asyncio.run(read())
    assert "ontology://demo-mcp/contracts.ttl" in uris
    triples = set(parse_triples(text))
    step = "urn:scion:demo-mcp:step:list-demo-items.1"
    assert (
        step,
        CONTRACT_NS + "callsTool",
        "urn:scion:demo-mcp:tool:demo_list_items",
    ) in triples
    assert (
        "urn:scion:demo-mcp:prompt:demo-curator",
        CONTRACT_NS + "businessRole",
        "urn:scion:demo-mcp:role:demo_curator",
    ) in triples
    assert (
        "urn:scion:demo-mcp:prompt:demo",
        CONTRACT_NS + "readsLocation",
        "urn:scion:demo-mcp:location:demo-items",
    ) in triples
    assert prompts["demo"]["team.knuckles/prompt"]["agent"]["kind"] == "operator"
    assert surface_check("democonn") == []
    assert check_main(["democonn"]) == 0


_ROUTED_TOOLS = """\
from typing import Any


def register_route_tools(mcp: Any) -> None:
    @mcp.tool
    def demo_route(action: str) -> str:
        return action
"""


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.6")
def test_self_check_flags_an_action_routed_tool(demo) -> None:
    init = (
        "from scion_sdk.connector import Connector\n"
        "from democonn.client import Api, get_client\n"
        "connector = Connector(api=Api, get_client=get_client, tools='democonn.routed', instructions='Demo.')\n"
    )
    demo(init=init, extra_files={"routed.py": _ROUTED_TOOLS})
    found = surface_check("democonn")
    assert "tool demo_route routes an action parameter (condensed tool)" in found
    assert check_main(["democonn"]) == 1


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.7")
def test_complete_drafts_a_full_declaration_for_a_bare_connector(demo) -> None:
    root = demo(contracts=None)
    (root / "democonn/prompts/demo-curator.json").unlink()
    (root / "democonn/prompts/demo.json").write_text(
        json.dumps(
            {
                "identity": {"role": "r", "goal": "g"},
                "instructions": {"core_directive": "Run demo_list_items."},
            }
        )
    )
    source = complete("democonn")
    assert source == complete("democonn")
    namespace: dict = {}
    exec(compile(source, "connector_contracts.py", "exec"), namespace)
    contract = namespace["CONTRACTS"][0]
    assert contract.task == "demo-list-items"
    assert contract.steps[0].result == ("id",) or contract.steps[0].result
    assert {r.code for r in contract.steps[0].refusals} == {
        "ACCESS_DENIED",
        "NOT_FOUND",
    }
    assert {link.task for link in namespace["LINKS"]} >= {"demo", "demo-skill"}
    assert namespace["LOCATIONS"][0].holds == ("DemoItem",)
    assert namespace["ROLES"][0].name == "demo administrator"
    prompts = complete_prompts("democonn")
    assert set(prompts) == {
        "prompts/demo-mcp-operator.json",
        "prompts/demo-mcp-demo-administrator.json",
    }
    drafted = json.loads(prompts["prompts/demo-mcp-operator.json"])
    assert drafted["agent"]["tools"] == ["demo_list_items"]
    assert main(["complete", "democonn", "--write"]) == 0
    assert (root / "democonn/connector_contracts.py").is_file()
    assert (root / "democonn/prompts/demo-mcp-operator.json").is_file()
    assert main(["complete"]) == 2


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.7")
def test_complete_keeps_hand_written_entries(demo) -> None:
    demo()
    namespace: dict = {}
    exec(compile(complete("democonn"), "c.py", "exec"), namespace)
    tasks = [c.task for c in namespace["CONTRACTS"]]
    assert tasks == ["list-demo-items"]
    assert [r.name for r in namespace["ROLES"]] == ["demo curator"]
    assert complete_prompts("democonn") == {}
    assert "ToolUseContract(" in CONTRACTS


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.3")
def test_no_declaration_serves_no_contracts_resource(demo, tmp_path: Path) -> None:
    from scion_sdk.mcp.content import ConnectorContent, contracts_resource

    demo(contracts=None)
    content = ConnectorContent(
        connector="demo-mcp", package_root=tmp_path / "democonn", package_version="1"
    )
    assert contracts_resource(content) is None


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R916.7")
def test_private_helpers_agree_with_the_public_entry_points(demo) -> None:
    from scion_sdk import _surface_agents, _surface_complete
    from scion_sdk.connector import build_server
    from scion_sdk.surface_contracts import PLATFORM_PROMPTS
    from scion_sdk.testing.surface_check import ServedSurface, served_surface

    demo()
    assert "connector-status" in PLATFORM_PROMPTS
    assert _surface_agents.AGENT_KINDS == ("operator", "business-role")
    surface = served_surface(build_server("democonn", command_args=[])[1])
    assert isinstance(surface, ServedSurface)
    decl = load_surface("democonn")
    assert (
        _surface_agents.agent_gaps(
            decl, surface.prompt_documents, surface.tools, surface.skills
        )
        == []
    )
    assert (
        _surface_agents.prompt_gaps(
            "demo",
            surface.prompt_documents["demo"],
            decl,
            surface.tools,
            surface.skills,
        )
        == []
    )
    assert _surface_complete.draft_contracts("democonn") == complete("democonn")
    assert _surface_complete.draft_prompts("democonn") == {}
    assert _surface_complete.render_source(decl).startswith('"""Surface contracts')
    drafts = _surface_complete.write_drafts("democonn", write=False)
    assert [path for path, _ in drafts] == ["connector_contracts.py"]
