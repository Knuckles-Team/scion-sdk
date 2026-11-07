"""Draft a complete surface declaration for an existing connector (R916.7, R916.8).

:func:`complete` builds the connector server in process, lists what it
serves, and merges that with the package's ``connector_contracts.py``. Every
hand-written entry is kept by value. Each served tool without a contract step
gets one :class:`ToolUseContract`; each served prompt and skill without a link
gets one :class:`SurfaceLink`; each declared source without a location gets
one :class:`DataLocation`. :func:`complete_prompts` drafts an operator prompt and
a business-role prompt when the connector has none. Output is deterministic.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from scion_sdk.surface_contracts import (
    BusinessRole,
    DataLocation,
    Refusal,
    SurfaceDeclaration,
    SurfaceLink,
    ToolStep,
    ToolUseContract,
    load_surface,
)

_NEVER = "expose a credential or a secret value"
_OUTPUT = "a summary of each action taken and its result"


@dataclass(frozen=True)
class _Tool:
    name: str
    params: tuple[str, ...]
    result: tuple[str, ...]
    read_only: bool
    destructive: bool


@dataclass(frozen=True)
class _Served:
    connector: str
    root: Path
    tools: tuple[_Tool, ...]
    prompts: dict[str, dict[str, Any]]
    skills: dict[str, str]
    sources: tuple[tuple[str, str, str], ...]


def _package_root(package: str) -> Path:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise ValueError(f"{package} is not an importable package")
    return Path(next(iter(spec.submodule_search_locations)))


def _result_fields(schema: Mapping[str, Any] | None) -> tuple[str, ...]:
    schema = schema or {}
    if schema.get("x-fastmcp-wrap-result"):
        schema = (schema.get("properties") or {}).get("result") or {}
    if schema.get("type") == "array":
        schema = schema.get("items") or {}
    keys = sorted((schema.get("properties") or {}).keys())
    return tuple(keys) or ("result",)


def _tool(listed: Any) -> _Tool:
    annotations = listed.annotations
    params = sorted(
        k for k in ((listed.input_schema or {}).get("properties") or {}) if k != "ctx"
    )
    return _Tool(
        name=listed.name,
        params=tuple(params),
        result=_result_fields(listed.output_schema),
        read_only=bool(getattr(annotations, "read_only_hint", False)),
        destructive=bool(getattr(annotations, "destructive_hint", False)),
    )


def _sources(text: str) -> tuple[tuple[str, str, str], ...]:
    declaration = json.loads(text)
    mappings = declaration.get("schema_mappings") or {}
    found = []
    for source in declaration.get("sources") or []:
        record = str(source.get("record_type") or "")
        held = (mappings.get(record) or {}).get("ontology_class") or record
        tools = source.get("tools") or []
        if source.get("source_id") and held and tools:
            found.append((str(source["source_id"]), str(held), str(tools[0])))
    return tuple(sorted(found))


def _serve(package: str) -> _Served:
    from fastmcp import Client

    from scion_sdk.connector import build_server

    mcp = build_server(package, command_args=[])[1]
    root = _package_root(package)

    async def read() -> tuple[list[Any], set[str], str]:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            uris = {str(r.uri) for r in await client.list_resources()}
            text = ""
            if "connector://resources" in uris:
                text = (await client.read_resource("connector://resources"))[0].text
            return tools, uris, text

    tools, uris, text = asyncio.run(read())
    skills = {
        uri.removeprefix("skill://").split("/", 1)[0]
        for uri in uris
        if uri.startswith("skill://")
    }
    return _Served(
        connector=mcp.name,
        root=root,
        tools=tuple(sorted((_tool(t) for t in tools), key=lambda t: t.name)),
        prompts=_prompt_documents(root),
        skills={name: _skill_text(root, name) for name in sorted(skills)},
        sources=_sources(text) if text else (),
    )


def _prompt_documents(root: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "prompts").glob("*.json")):
        if not path.name.startswith("_"):
            document = json.loads(path.read_text(encoding="utf-8"))
            found[path.stem] = document if isinstance(document, dict) else {}
    return found


def _skill_text(root: Path, name: str) -> str:
    path = root / "skills" / name / "SKILL.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# ── drafting ────────────────────────────────────────────────────────────────


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _task(tool: str) -> str:
    return tool.replace("_", "-")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _refusals(tool: _Tool) -> tuple[Refusal, ...]:
    found = [Refusal("ACCESS_DENIED", "the credential lacks access to the operation")]
    if tool.read_only:
        found.append(Refusal("NOT_FOUND", "the requested record does not exist"))
    else:
        found.append(
            Refusal("INVALID_ARGUMENT", "the upstream system rejects the input")
        )
    if tool.destructive:
        found.append(
            Refusal(
                "CONFIRMATION_REQUIRED",
                "the caller has not confirmed the destructive call",
            )
        )
    return tuple(found)


def _drafted_contracts(
    served: _Served, existing: SurfaceDeclaration
) -> list[ToolUseContract]:
    stepped = {s.tool for c in existing.contracts for s in c.steps}
    return [
        ToolUseContract(
            task=_task(tool.name),
            steps=(ToolStep(tool.name, tool.params, tool.result, _refusals(tool)),),
        )
        for tool in served.tools
        if tool.name not in stepped
    ]


def _mentioned(text: str, tools: Iterable[str]) -> tuple[str, ...]:
    return tuple(t for t in tools if re.search(rf"\b{re.escape(t)}\b", text))


def _skill_tools(name: str, text: str, tools: tuple[str, ...]) -> tuple[str, ...]:
    mentioned = _mentioned(text, tools)
    if mentioned:
        return mentioned
    tokens = set(name.split("-"))
    shared = tuple(t for t in tools if tokens & set(t.split("_")[1:]))
    return shared or tools


def _prompt_tools(
    document: Mapping[str, Any], tools: tuple[str, ...]
) -> tuple[str, ...]:
    agent = _mapping(document.get("agent"))
    named = tuple(t for t in agent.get("tools") or () if t in tools)
    if named:
        return named
    instructions = _mapping(document.get("instructions"))
    return _mentioned(str(instructions.get("core_directive") or ""), tools) or tools


def _drafted_links(
    served: _Served,
    existing: SurfaceDeclaration,
    prompts: Mapping[str, Mapping[str, Any]],
) -> list[SurfaceLink]:
    tools = tuple(t.name for t in served.tools)
    linked_prompts = {p for link in existing.links for p in link.prompts}
    linked_skills = {s for link in existing.links for s in link.skills}
    found = [
        SurfaceLink(task=name, tools=_prompt_tools(doc, tools), prompts=(name,))
        for name, doc in sorted(prompts.items())
        if name not in linked_prompts
    ]
    found += [
        SurfaceLink(task=name, tools=_skill_tools(name, text, tools), skills=(name,))
        for name, text in served.skills.items()
        if name not in linked_skills
    ]
    return found


def _instance(served: _Served) -> str:
    example = served.root.parent / ".env.example"
    if example.is_file():
        for line in example.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*([A-Z][A-Z0-9_]*URL)\s*=", line)
            if match:
                return match.group(1)
    return f"{served.connector}-default"


def _drafted_locations(
    served: _Served, existing: SurfaceDeclaration
) -> list[DataLocation]:
    known = {loc.name for loc in existing.locations}
    instance = _instance(served)
    return [
        DataLocation(name=source_id, holds=(held,), instance=instance, read_tool=tool)
        for source_id, held, tool in served.sources
        if source_id not in known
    ]


def _agent(
    kind: str, served: _Served, decl: SurfaceDeclaration, role: str = ""
) -> dict[str, Any]:
    tools = sorted(
        {s.tool for c in decl.contracts for s in c.steps}
        & {t.name for t in served.tools}
    )
    agent: dict[str, Any] = {"kind": kind}
    if role:
        agent["business_role"] = role
    destructive = sorted(
        t.name for t in served.tools if t.destructive and t.name in tools
    )
    agent.update(
        tools=tools,
        skills=sorted(served.skills),
        data={"reads": sorted(loc.name for loc in decl.locations), "writes": []},
        guardrails={
            "approval_required": [f"call {t}" for t in destructive],
            "never": [_NEVER],
        },
        outputs=[_OUTPUT],
    )
    return agent


def _prompt_document(
    served: _Served, decl: SurfaceDeclaration, role: str = ""
) -> dict[str, Any]:
    kind = "business-role" if role else "operator"
    who = role or f"{served.connector} operator"
    return {
        "title": who.title(),
        "description": f"Agent definition for the {who}.",
        "identity": {
            "role": who,
            "goal": f"Operate {served.connector} through its served tools.",
        },
        "instructions": {
            "core_directive": f"You are the {who}. Use only the listed tools and skills, "
            "follow each tool-use contract in order, and report every result."
        },
        "agent": _agent(kind, served, decl, role),
    }


@dataclass(frozen=True)
class _Plan:
    declaration: SurfaceDeclaration
    prompts: dict[str, dict[str, Any]]
    root: Path


def _role_prompts(
    served: _Served, decl: SurfaceDeclaration, prompts: Mapping[str, Mapping[str, Any]]
) -> tuple[list[BusinessRole], dict[str, dict[str, Any]]]:
    roles = list(decl.roles)
    if not roles:
        domain = served.connector.rsplit("-", 1)[0]
        roles.append(
            BusinessRole(
                name=f"{domain} administrator",
                tasks=tuple(sorted(c.task for c in decl.contracts)),
            )
        )
    agents = [_mapping(d.get("agent")) for d in prompts.values()]
    agents = [a for a in agents if a]
    served_roles = {
        a.get("business_role") for a in agents if a.get("kind") == "business-role"
    }
    drafts = {
        f"{_slug(served.connector)}-{_slug(r.name)}": _prompt_document(
            served, decl, r.name
        )
        for r in roles
        if r.name not in served_roles
    }
    if not any(a.get("kind") == "operator" for a in agents):
        drafts[f"{_slug(served.connector)}-operator"] = _prompt_document(served, decl)
    return roles, drafts


def _plan(package: str) -> _Plan:
    served = _serve(package)
    existing = load_surface(package, connector=served.connector) or SurfaceDeclaration(
        served.connector
    )
    base = SurfaceDeclaration(
        connector=served.connector,
        contracts=(*existing.contracts, *_drafted_contracts(served, existing)),
        links=existing.links,
        locations=(*existing.locations, *_drafted_locations(served, existing)),
        roles=existing.roles,
    )
    roles, drafts = _role_prompts(served, base, served.prompts)
    prompts = {**served.prompts, **drafts}
    declaration = SurfaceDeclaration(
        connector=served.connector,
        contracts=base.contracts,
        links=(*existing.links, *_drafted_links(served, existing, prompts)),
        locations=base.locations,
        roles=tuple(roles),
    )
    return _Plan(declaration=declaration, prompts=drafts, root=served.root)


# ── source rendering ────────────────────────────────────────────────────────


def _code(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, tuple):
        if not value:
            return "()"
        items = "".join(f"{pad}    {_code(v, indent + 4)},\n" for v in value)
        return f"(\n{items}{pad})"
    if hasattr(value, "__dataclass_fields__"):
        args = [
            f"{pad}    {f.name}={_code(getattr(value, f.name), indent + 4)},\n"
            for f in fields(value)
            if getattr(value, f.name) != f.default
        ]
        return f"{type(value).__name__}(\n{''.join(args)}{pad})"
    return json.dumps(value)


_HEADER = '''"""Surface contracts: tool use, links, data locations and business roles.

Drafted by ``python -m scion_sdk.testing.surface_draft complete``; edit freely.
"""
'''


def _used(value: Any, found: set[str]) -> set[str]:
    if isinstance(value, tuple):
        for item in value:
            _used(item, found)
    elif hasattr(value, "__dataclass_fields__"):
        found.add(type(value).__name__)
        for f in fields(value):
            _used(getattr(value, f.name), found)
    return found


def render_source(declaration: SurfaceDeclaration) -> str:
    """The ``connector_contracts.py`` source that declares ``declaration``."""
    tables = (
        ("CONTRACTS", declaration.contracts),
        ("LINKS", declaration.links),
        ("LOCATIONS", declaration.locations),
        ("ROLES", declaration.roles),
    )
    used = sorted(_used(tuple(v for _, v in tables), set()))
    imports = "".join(f"    {name},\n" for name in used)
    parts = [
        _HEADER,
        f"from scion_sdk.surface_contracts import (\n{imports})\n" if used else "",
    ]
    parts += [f"{name} = {_code(tuple(values))}\n" for name, values in tables]
    return "\n".join(p for p in parts if p)


def complete(package: str) -> str:
    """The complete ``connector_contracts.py`` source of ``package``."""
    return render_source(_plan(package).declaration)


def _prompt_text(document: Mapping[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def complete_prompts(package: str) -> dict[str, str]:
    """Missing prompt files of ``package`` as ``prompts/<name>.json`` → JSON text."""
    plan = _plan(package)
    return {
        f"prompts/{name}.json": _prompt_text(doc)
        for name, doc in sorted(plan.prompts.items())
    }


def write_drafts(package: str, *, write: bool) -> list[tuple[str, str]]:
    """Each drafted file as (package-relative path, text); written when ``write``."""
    plan = _plan(package)
    drafts = [("connector_contracts.py", render_source(plan.declaration))]
    drafts += [
        (f"prompts/{name}.json", _prompt_text(doc))
        for name, doc in sorted(plan.prompts.items())
    ]
    if write:
        for relative, text in drafts:
            target = plan.root / relative
            if relative.startswith("prompts/") and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    return drafts


def main(argv: list[str] | None = None) -> int:
    """``complete <package> [--write]``: print or write the drafted declaration."""
    args = sys.argv[1:] if argv is None else argv
    if (
        len(args) not in (2, 3)
        or args[0] != "complete"
        or args[2:] not in ([], ["--write"])
    ):
        sys.stderr.write(
            "usage: python -m scion_sdk.testing.surface_draft complete <package> [--write]\n"
        )
        return 2
    for path, text in write_drafts(args[1], write=bool(args[2:])):
        sys.stdout.write(f"# {path}\n{text}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
