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
* ``UPSTREAM_TOOLS``: :class:`UpstreamTool` values. A connector that proxies a
  provider's own MCP server registers no tool offline; it declares the tools
  the upstream serves, and :func:`surface_findings` treats each declared tool
  as served (SCION-CONNECTOR-CONTROL-R916.9).

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
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from typing import Any

from scion_sdk.ontology_namespaces import (
    CORE_NAMESPACE,
    SURFACE_CONTRACT_NAMESPACE,
)

__all__ = [
    "CONTRACT_NS",
    "CONTRIBUTOR_SKILL_SUFFIX",
    "CORE_NS",
    "PLATFORM_PROMPTS",
    "BusinessRole",
    "DataLocation",
    "Refusal",
    "SurfaceDeclaration",
    "SurfaceLink",
    "ToolStep",
    "ToolUseContract",
    "UpstreamTool",
    "load_surface",
    "prompt_agent_findings",
    "render_surface_turtle",
    "render_surface_turtle_shards",
    "surface_contract_vocabulary",
    "surface_findings",
]

CONTRACT_NS = SURFACE_CONTRACT_NAMESPACE
CORE_NS = CORE_NAMESPACE
_CONTENT_SCHEMES = ("skill://", "ontology://", "shapes://")
#: Prompts every SDK server registers itself; a connector links none of them.
PLATFORM_PROMPTS = frozenset({"connector-status"})
#: A skill named ``<name>-development`` guides contributors; it serves no task, so it needs no link.
CONTRIBUTOR_SKILL_SUFFIX = "-development"


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
class UpstreamTool:
    """One tool an upstream MCP server serves, declared so it can be checked offline.

    The served name is ``<upstream>_<tool>`` (SCION-CONNECTOR-CONTROL-R914.5).
    ``input_schema`` is the upstream's JSON Schema for the tool arguments and
    ``destructive`` mirrors the upstream's destructive annotation.
    """

    upstream: str
    tool: str
    description: str
    input_schema: Mapping[str, Any]
    destructive: bool = False

    @property
    def name(self) -> str:
        """The name the connector serves the tool under."""
        return f"{self.upstream}_{self.tool}"


@dataclass(frozen=True)
class SurfaceDeclaration:
    """The contracts, links, locations, roles and upstream tools of one connector."""

    connector: str
    contracts: tuple[ToolUseContract, ...] = ()
    links: tuple[SurfaceLink, ...] = ()
    locations: tuple[DataLocation, ...] = ()
    roles: tuple[BusinessRole, ...] = ()
    upstream_tools: tuple[UpstreamTool, ...] = ()

    def __post_init__(self) -> None:
        for name in ("contracts", "links", "locations", "roles", "upstream_tools"):
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
        upstream_tools=tuple(getattr(module, "UPSTREAM_TOOLS", ())),
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


_PN_LOCAL = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_:.-]*[A-Za-z0-9_:-])?")


class _Graph:
    def __init__(self, connector: str) -> None:
        self.connector = connector
        self.subjects: dict[str, list[tuple[str, str]]] = {}

    def iri(self, kind: str, name: str) -> str:
        """The node IRI, written as a prefixed name under ``u:`` when the local part allows it (smaller body)."""
        local = f"{kind}:{_segment(name)}"
        if _PN_LOCAL.fullmatch(local):
            return f"u:{local}"
        return f"<urn:scion:{_segment(self.connector)}:{local}>"

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
        return self.turtle_shards(None)[0]

    def turtle_shards(self, limit: int | None) -> list[str]:
        """The Turtle text, cut at subject boundaries into documents of at most ``limit`` bytes (each self-contained)."""
        header = self._header()
        shards: list[list[str]] = [[]]
        size = len(header.encode())
        for subject in sorted(self.subjects):
            pairs = sorted(self.subjects[subject], key=lambda p: (p[0] != "a", p))
            body = " ;\n    ".join(f"{p} {o}" for p, o in pairs)
            block = f"{subject}\n    {body} .\n"
            cost = len(block.encode()) + 1
            if limit is not None and shards[-1] and size + cost > limit:
                shards.append([])
                size = len(header.encode())
            shards[-1].append(block)
            size += cost
        return [header + "\n".join(blocks) for blocks in shards]

    def _header(self) -> str:
        lines = [
            f"@prefix u: <urn:scion:{_segment(self.connector)}:> .",
            f"@prefix kg: <{CORE_NS}> .",
            f"@prefix sc: <{CONTRACT_NS}> .",
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
            "",
        ]
        return "\n".join(lines) + "\n"


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
    return _surface_graph(declaration, prompts).turtle()


def render_surface_turtle_shards(
    declaration: SurfaceDeclaration,
    prompts: Mapping[str, Mapping[str, Any]] | None = None,
    limit: int = 1_500_000,
) -> list[str]:
    """The same Turtle cut into self-contained documents of at most ``limit`` bytes (a connector pack bounds each body)."""
    return _surface_graph(declaration, prompts).turtle_shards(limit)


def _surface_graph(
    declaration: SurfaceDeclaration,
    prompts: Mapping[str, Mapping[str, Any]] | None,
) -> _Graph:
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
    return graph


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


def _upstream_tool_gaps(declaration: SurfaceDeclaration, local: set[str]) -> list[str]:
    """Names, descriptions, schemas and the destructive flag of the declared upstream tools."""
    found: list[str] = []
    seen: set[str] = set()
    for tool in declaration.upstream_tools:
        name = tool.name
        if not tool.upstream.strip() or not tool.tool.strip():
            found.append(f"upstream tool {name!r} names no upstream or no tool")
        if name in seen:
            found.append(f"upstream tool {name} is declared twice")
        seen.add(name)
        if name in local:
            found.append(f"upstream tool {name} collides with a local tool")
        if not tool.description.strip():
            found.append(f"upstream tool {name} has no description")
        schema = tool.input_schema
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            found.append(f"upstream tool {name} has no object input schema")
        elif not isinstance(schema.get("properties", {}), Mapping):
            found.append(f"upstream tool {name} has a malformed input schema")
        # Declarations are loaded from connector modules, so the flag is checked at run time.
        flag: object = tool.destructive
        if not isinstance(flag, bool):
            found.append(f"upstream tool {name} has no destructive annotation")
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
    task_skills = {s for s in skills if not s.endswith(CONTRIBUTOR_SKILL_SUFFIX)}
    for kind, served in (("prompt", prompts), ("skill", task_skills)):
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
    local = set(tools)
    schemas = dict(tool_schemas or {})
    if declaration is not None:
        # A declared upstream tool counts as served; it is checked like a registered one.
        tools = local | {t.name for t in declaration.upstream_tools}
        schemas.update(
            {
                t.name: t.input_schema
                for t in declaration.upstream_tools
                if isinstance(t.input_schema, Mapping)
            }
        )
    found = _surface_gaps(
        tools=tools,
        prompts=prompts,
        skills=skills,
        resources=resources,
        tool_schemas=schemas,
    )
    if declaration is None:
        found.append("no connector_contracts declaration")
    else:
        found += _upstream_tool_gaps(declaration, local)
        found += _contract_gaps(declaration, tools)
        found += _link_gaps(declaration, tools, prompts, skills)
        found += _location_gaps(declaration, tools)
    if prompt_documents is not None:
        from scion_sdk.surface_contracts._agents import agent_gaps

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
    from scion_sdk.surface_contracts._agents import prompt_gaps

    return prompt_gaps(name, document, declaration, served_tools, skills)
