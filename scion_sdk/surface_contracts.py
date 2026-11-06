"""Connector surface contracts: tool use, surface links and data locations.

A connector declares, in ``<package>/connector_contracts.py``, three tuples
(SCION-CONNECTOR-CONTROL-R916):

* ``CONTRACTS``: :class:`ToolUseContract` values. Each names one task and the
  ordered :class:`ToolStep` calls that execute it, with their parameters,
  result fields and :class:`Refusal` shapes.
* ``LINKS``: :class:`SurfaceLink` values. Each joins prompts, skills and tools
  to the task they serve.
* ``LOCATIONS``: :class:`DataLocation` values. Each states which instance or
  store holds which ontology classes, and which tool reads them, so routing
  and federation resolve through the ontology.
* ``ROLES``: :class:`BusinessRole` values. Each names one business role the
  connector serves; a business-role prompt defines the agent for it.

Each packaged prompt is a complete agent definition: its ``agent`` object
names the kind, the tools, skills, data locations, guardrails and outputs
(SCION-CONNECTOR-CONTROL-R916.8, :func:`prompt_agent_findings`).

:func:`render_surface_turtle` writes the declaration as Turtle individuals
typed by the vocabulary in ``ontology/surface_contract.ttl``. Every class of
that vocabulary extends a core class; neither file imports the core ontology.
:func:`surface_findings` lists what a served surface lacks.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

__all__ = [
    "CONTRACT_NS",
    "CORE_NS",
    "PLATFORM_PROMPTS",
    "BusinessRole",
    "DataLocation",
    "Refusal",
    "SurfaceDeclaration",
    "SurfaceLink",
    "ToolStep",
    "ToolUseContract",
    "complete",
    "complete_prompts",
    "load_surface",
    "prompt_agent_findings",
    "render_surface_turtle",
    "surface_contract_vocabulary",
    "surface_findings",
]

CONTRACT_NS = "https://knuckles-team.github.io/scion-sdk/contract#"
CORE_NS = "http://knuckles.team/kg#"
_CONTENT_SCHEMES = ("skill://", "ontology://", "shapes://")
#: Prompts every SDK server registers itself; a connector links none of them.
PLATFORM_PROMPTS = frozenset({"connector-status"})


def _strings(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        return (values,)
    return tuple(str(v) for v in values)


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


@dataclass(frozen=True)
class Refusal:
    """One refusal a step can return: its code and the condition."""

    code: str
    when: str = ""


@dataclass(frozen=True)
class ToolStep:
    """One ordered tool call of a task."""

    tool: str
    params: tuple[str, ...] = ()
    result: tuple[str, ...] = ()
    refusals: tuple[Refusal, ...] = ()

    def __post_init__(self) -> None:
        _set(self, "params", _strings(self.params))
        _set(self, "result", _strings(self.result))
        _set(self, "refusals", tuple(self.refusals))


@dataclass(frozen=True)
class ToolUseContract:
    """Which tools a task uses, in what order."""

    task: str
    steps: tuple[ToolStep, ...]
    description: str = ""

    def __post_init__(self) -> None:
        _set(self, "steps", tuple(self.steps))


@dataclass(frozen=True)
class SurfaceLink:
    """Prompts, skills and tools joined to the task they serve."""

    task: str
    tools: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("tools", "prompts", "skills"):
            _set(self, name, _strings(getattr(self, name)))


@dataclass(frozen=True)
class DataLocation:
    """Which instance or store holds which classes, and the tool that reads them."""

    name: str
    holds: tuple[str, ...]
    instance: str
    read_tool: str
    store: str = ""
    route_param: str = ""
    federation_key: str = ""

    def __post_init__(self) -> None:
        _set(self, "holds", _strings(self.holds))


@dataclass(frozen=True)
class BusinessRole:
    """One business role the connector serves, and the tasks it performs."""

    name: str
    tasks: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _set(self, "tasks", _strings(self.tasks))


@dataclass(frozen=True)
class SurfaceDeclaration:
    """The contracts, links, locations and roles of one connector."""

    connector: str
    contracts: tuple[ToolUseContract, ...] = ()
    links: tuple[SurfaceLink, ...] = ()
    locations: tuple[DataLocation, ...] = ()
    roles: tuple[BusinessRole, ...] = ()

    def __post_init__(self) -> None:
        for name in ("contracts", "links", "locations", "roles"):
            _set(self, name, tuple(getattr(self, name)))


def load_surface(
    package: str, connector: str | None = None
) -> SurfaceDeclaration | None:
    """Read ``<package>.connector_contracts``; ``None`` when the module is absent.

    The connector name defaults to the package's declared fleet name.
    """
    module_name = f"{package}.connector_contracts"
    try:
        found = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        return None
    if found is None:
        return None
    module = importlib.import_module(module_name)
    if connector is None:
        from scion_sdk.connector_identity import connector_name

        connector = connector_name(package)
    return SurfaceDeclaration(
        connector=connector,
        contracts=tuple(getattr(module, "CONTRACTS", ())),
        links=tuple(getattr(module, "LINKS", ())),
        locations=tuple(getattr(module, "LOCATIONS", ())),
        roles=tuple(getattr(module, "ROLES", ())),
    )


def surface_contract_vocabulary() -> str:
    """Return the packaged surface-contract vocabulary as Turtle text."""
    path = resources.files("scion_sdk") / "ontology" / "surface_contract.ttl"
    return path.read_text(encoding="utf-8")


# ── Turtle rendering ────────────────────────────────────────────────────────


def _literal(value: str | int) -> str:
    if isinstance(value, int):
        return str(value)
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _segment(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)


class _Graph:
    def __init__(self, connector: str) -> None:
        self.connector = connector
        self.subjects: dict[str, list[tuple[str, str]]] = {}

    def iri(self, kind: str, name: str) -> str:
        return f"<urn:scion:{_segment(self.connector)}:{kind}:{_segment(name)}>"

    def add(self, subject: str, predicate: str, obj: str) -> None:
        pairs = self.subjects.setdefault(subject, [])
        if (predicate, obj) not in pairs:
            pairs.append((predicate, obj))

    def node(self, kind: str, name: str, core_class: str) -> str:
        subject = self.iri(kind, name)
        self.add(subject, "a", f"kg:{core_class}")
        self.add(subject, "rdfs:label", _literal(name))
        return subject

    def turtle(self) -> str:
        lines = [
            f"@prefix kg: <{CORE_NS}> .",
            f"@prefix sc: <{CONTRACT_NS}> .",
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            "",
        ]
        for subject in sorted(self.subjects):
            pairs = sorted(self.subjects[subject], key=lambda p: (p[0] != "a", p))
            body = " ;\n    ".join(f"{p} {o}" for p, o in pairs)
            lines.append(f"{subject}\n    {body} .\n")
        return "\n".join(lines)


def _holds_iri(connector: str, value: str) -> str:
    if ":" in value:
        return f"<{value}>"
    return f"<urn:scion:{_segment(connector)}:class:{_segment(value)}>"


def _render_contract(graph: _Graph, contract: ToolUseContract) -> None:
    task = graph.node("task", contract.task, "Task")
    subject = graph.iri("contract", contract.task)
    graph.add(subject, "a", "sc:ToolUseContract")
    graph.add(subject, "sc:forTask", task)
    if contract.description:
        graph.add(subject, "rdfs:comment", _literal(contract.description))
    previous: str | None = None
    for order, step in enumerate(contract.steps, start=1):
        step_iri = graph.iri("step", f"{contract.task}.{order}")
        graph.add(subject, "kg:hasStep", step_iri)
        graph.add(step_iri, "a", "sc:ToolStep")
        graph.add(step_iri, "sc:stepOrder", _literal(order))
        graph.add(step_iri, "sc:callsTool", graph.node("tool", step.tool, "Tool"))
        for param in step.params:
            graph.add(step_iri, "sc:param", _literal(param))
        for result in step.result:
            graph.add(step_iri, "sc:resultField", _literal(result))
        for refusal in step.refusals:
            refusal_iri = graph.iri(
                "refusal", f"{contract.task}.{order}.{refusal.code}"
            )
            graph.add(step_iri, "sc:refusal", refusal_iri)
            graph.add(refusal_iri, "a", "sc:Refusal")
            graph.add(refusal_iri, "sc:refusalCode", _literal(refusal.code))
            if refusal.when:
                graph.add(refusal_iri, "sc:refusalWhen", _literal(refusal.when))
        if previous is not None:
            graph.add(previous, "kg:precedes", step_iri)
        previous = step_iri


def _render_link(graph: _Graph, link: SurfaceLink) -> None:
    task = graph.node("task", link.task, "Task")
    tools = [graph.node("tool", name, "Tool") for name in link.tools]
    for tool in tools:
        graph.add(tool, "sc:servesTask", task)
    for kind, names, core in (
        ("prompt", link.prompts, "Prompt"),
        ("skill", link.skills, "Skill"),
    ):
        for name in names:
            subject = graph.node(kind, name, core)
            graph.add(subject, "sc:servesTask", task)
            for tool in tools:
                graph.add(subject, "sc:usesTool", tool)


def _render_location(graph: _Graph, location: DataLocation) -> None:
    subject = graph.iri("location", location.name)
    graph.add(subject, "a", "sc:DataLocation")
    graph.add(subject, "rdfs:label", _literal(location.name))
    for held in location.holds:
        graph.add(subject, "sc:holdsClass", _holds_iri(graph.connector, held))
    graph.add(subject, "sc:instance", _literal(location.instance))
    graph.add(subject, "sc:readTool", graph.node("tool", location.read_tool, "Tool"))
    for predicate, value in (
        ("sc:store", location.store),
        ("sc:routeParam", location.route_param),
        ("sc:federationKey", location.federation_key),
    ):
        if value:
            graph.add(subject, predicate, _literal(value))


def _render_role(graph: _Graph, role: BusinessRole) -> None:
    subject = graph.iri("role", role.name)
    graph.add(subject, "a", "sc:BusinessRole")
    graph.add(subject, "rdfs:label", _literal(role.name))
    if role.description:
        graph.add(subject, "rdfs:comment", _literal(role.description))
    for task in role.tasks:
        graph.add(subject, "sc:servesTask", graph.node("task", task, "Task"))


def _names(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list | tuple) else []


def _render_agent(graph: _Graph, name: str, document: Mapping[str, Any]) -> None:
    agent = document.get("agent")
    if not isinstance(agent, Mapping):
        return
    subject = graph.node("prompt", name, "Prompt")
    if agent.get("kind"):
        graph.add(subject, "sc:agentKind", _literal(str(agent["kind"])))
    if agent.get("business_role"):
        graph.add(
            subject, "sc:businessRole", graph.iri("role", str(agent["business_role"]))
        )
    for tool in _names(agent.get("tools")):
        graph.add(subject, "sc:usesTool", graph.node("tool", tool, "Tool"))
    for skill in _names(agent.get("skills")):
        graph.add(subject, "sc:usesSkill", graph.node("skill", skill, "Skill"))
    raw = agent.get("data")
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    for key, predicate in (
        ("reads", "sc:readsLocation"),
        ("writes", "sc:writesLocation"),
    ):
        for location in _names(data.get(key)):
            graph.add(subject, predicate, graph.iri("location", location))


def render_surface_turtle(
    declaration: SurfaceDeclaration,
    prompts: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Render ``declaration`` as deterministic Turtle individuals, with no imports.

    ``prompts`` maps each packaged prompt name to its JSON document; its
    ``agent`` object becomes the prompt's agent-definition triples.
    """
    graph = _Graph(declaration.connector)
    for contract in declaration.contracts:
        _render_contract(graph, contract)
    for link in declaration.links:
        _render_link(graph, link)
    for location in declaration.locations:
        _render_location(graph, location)
    for role in declaration.roles:
        _render_role(graph, role)
    for name in sorted(prompts or {}):
        _render_agent(graph, name, (prompts or {})[name])
    return graph.turtle()


# ── findings ────────────────────────────────────────────────────────────────


def _surface_gaps(
    *,
    tools: set[str],
    prompts: set[str],
    skills: set[str],
    resources: set[str],
    tool_schemas: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    found: list[str] = []
    if not tools:
        found.append("no tools served")
    for name in sorted(tool_schemas):
        properties = (tool_schemas[name] or {}).get("properties") or {}
        if "action" in properties:
            found.append(f"tool {name} routes an action parameter (condensed tool)")
    if not any(not uri.startswith(_CONTENT_SCHEMES) for uri in resources):
        found.append("no data resource served")
    if not prompts:
        found.append("no prompt served")
    if not skills:
        found.append("no skill served")
    if not any(uri.startswith("ontology://") for uri in resources):
        found.append("no ontology resource served")
    return found


def _contract_gaps(declaration: SurfaceDeclaration, tools: set[str]) -> list[str]:
    if not declaration.contracts:
        return ["no tool-use contract declared"]
    found: list[str] = []
    refusals = 0
    for contract in declaration.contracts:
        if not contract.steps:
            found.append(f"contract {contract.task} has no step")
        for order, step in enumerate(contract.steps, start=1):
            refusals += len(step.refusals)
            if step.tool not in tools:
                found.append(
                    f"contract {contract.task} step {order} names unserved tool {step.tool}"
                )
            if not step.result:
                found.append(
                    f"contract {contract.task} step {order} has no result fields"
                )
    if not refusals:
        found.append("no refusal declared in any contract")
    stepped = {
        step.tool for contract in declaration.contracts for step in contract.steps
    }
    for name in sorted(tools - stepped):
        found.append(f"tool {name} has no contract step")
    return found


def _link_gaps(
    declaration: SurfaceDeclaration,
    tools: set[str],
    prompts: set[str],
    skills: set[str],
) -> list[str]:
    found: list[str] = []
    linked: dict[str, set[str]] = {"prompt": set(), "skill": set()}
    for link in declaration.links:
        for kind, names, served in (
            ("tool", link.tools, tools),
            ("prompt", link.prompts, prompts),
            ("skill", link.skills, skills),
        ):
            for name in names:
                linked.setdefault(kind, set()).add(name)
                if name not in served:
                    found.append(f"link {link.task} names unserved {kind} {name}")
    for kind, served in (("prompt", prompts), ("skill", skills)):
        for name in sorted(served - linked[kind]):
            found.append(f"{kind} {name} is not linked to a task")
    covered = {
        t
        for link in declaration.links
        if link.prompts or link.skills
        for t in link.tools
    }
    for name in sorted(tools - covered):
        found.append(f"tool {name} is named by no prompt or skill link")
    return found


def _location_gaps(declaration: SurfaceDeclaration, tools: set[str]) -> list[str]:
    if not declaration.locations:
        return ["no data location declared"]
    found: list[str] = []
    for location in declaration.locations:
        if location.read_tool not in tools:
            found.append(
                f"location {location.name} reads through unserved tool {location.read_tool}"
            )
        if not location.holds:
            found.append(f"location {location.name} holds no class")
        if not location.instance:
            found.append(f"location {location.name} names no instance")
    return found


def surface_findings(
    declaration: SurfaceDeclaration | None,
    *,
    tools: set[str],
    prompts: set[str],
    skills: set[str],
    resources: set[str],
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
    prompt_documents: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """List, sorted, what the served surface and its declaration lack.

    The SDK's own :data:`PLATFORM_PROMPTS` are ignored. ``prompt_documents``
    maps each served prompt to its document (``identity`` and ``agent``); when
    given, each prompt is checked as an agent definition.
    """
    prompts = set(prompts) - PLATFORM_PROMPTS
    found = _surface_gaps(
        tools=tools,
        prompts=prompts,
        skills=skills,
        resources=resources,
        tool_schemas=tool_schemas or {},
    )
    if declaration is None:
        found.append("no connector_contracts declaration")
    else:
        found += _contract_gaps(declaration, tools)
        found += _link_gaps(declaration, tools, prompts, skills)
        found += _location_gaps(declaration, tools)
    if prompt_documents is not None:
        from scion_sdk._surface_agents import agent_gaps

        documents = {n: d for n, d in prompt_documents.items() if n in prompts}
        found += agent_gaps(declaration, documents, tools, skills)
    return sorted(set(found))


def prompt_agent_findings(
    document: Mapping[str, Any],
    declaration: SurfaceDeclaration | None,
    served_tools: set[str],
    skills: set[str],
    name: str = "prompt",
) -> list[str]:
    """List what one prompt document lacks as a complete agent definition."""
    from scion_sdk._surface_agents import prompt_gaps

    return prompt_gaps(name, document, declaration, served_tools, skills)


def complete(package: str) -> str:
    """Draft a complete ``connector_contracts.py`` for the connector ``package``.

    Hand-written entries are kept; served tools, prompts, skills and sources
    that nothing declares get a drafted entry. The output is deterministic.
    """
    from scion_sdk._surface_complete import draft_contracts

    return draft_contracts(package)


def complete_prompts(package: str) -> dict[str, str]:
    """Draft the missing operator and business-role prompt files of ``package``."""
    from scion_sdk._surface_complete import draft_prompts

    return draft_prompts(package)


def main(argv: list[str] | None = None) -> int:
    """``complete <package> [--write]``: print or write the drafted declaration."""
    import sys

    args = sys.argv[1:] if argv is None else argv
    if (
        len(args) not in (2, 3)
        or args[0] != "complete"
        or args[2:] not in ([], ["--write"])
    ):
        sys.stderr.write(
            "usage: python -m scion_sdk.surface_contracts complete <package> [--write]\n"
        )
        return 2
    from scion_sdk._surface_complete import write_drafts

    for path, text in write_drafts(args[1], write=bool(args[2:])):
        sys.stdout.write(f"# {path}\n{text}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
