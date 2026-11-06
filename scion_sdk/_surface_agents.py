"""Prompts as complete agent definitions (SCION-CONNECTOR-CONTROL-R916.8).

A packaged prompt document keeps ``title``, ``identity.role``,
``identity.goal`` and ``instructions.core_directive`` and adds ``agent``::

    {"kind": "operator" | "business-role",
     "business_role": "<role name, required for business-role>",
     "tools": [...], "skills": [...],
     "data": {"reads": [...], "writes": [...]},
     "guardrails": {"approval_required": [...], "never": [...]},
     "outputs": [...]}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scion_sdk.surface_contracts import PLATFORM_PROMPTS, SurfaceDeclaration

AGENT_KINDS = ("operator", "business-role")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _names(value: Any) -> list[str]:
    return (
        [str(v) for v in value if str(v).strip()]
        if isinstance(value, list | tuple)
        else []
    )


def _identity_gaps(name: str, document: Mapping[str, Any]) -> list[str]:
    identity = _mapping(document.get("identity"))
    return [
        f"prompt {name} has no identity.{key}"
        for key in ("role", "goal")
        if not str(identity.get(key) or "").strip()
    ]


def _kind_gaps(name: str, agent: Mapping[str, Any]) -> list[str]:
    kind = agent.get("kind")
    if kind not in AGENT_KINDS:
        return [f"prompt {name} agent.kind is not one of {', '.join(AGENT_KINDS)}"]
    if kind == "business-role" and not str(agent.get("business_role") or "").strip():
        return [f"prompt {name} is a business-role agent with no business_role"]
    return []


def _tool_gaps(
    name: str,
    agent: Mapping[str, Any],
    declaration: SurfaceDeclaration | None,
    served: set[str],
) -> list[str]:
    tools = _names(agent.get("tools"))
    if not tools:
        return [f"prompt {name} names no tool"]
    stepped = {
        s.tool for c in (declaration.contracts if declaration else ()) for s in c.steps
    }
    found = [f"prompt {name} names unserved tool {t}" for t in tools if t not in served]
    found += [
        f"prompt {name} names tool {t} that no contract step uses"
        for t in tools
        if t not in stepped
    ]
    return found


def _skill_gaps(name: str, agent: Mapping[str, Any], skills: set[str]) -> list[str]:
    named = _names(agent.get("skills"))
    if not named:
        return [f"prompt {name} names no skill"]
    return [
        f"prompt {name} names unpackaged skill {s}" for s in named if s not in skills
    ]


def _data_gaps(
    name: str, agent: Mapping[str, Any], declaration: SurfaceDeclaration | None
) -> list[str]:
    data = _mapping(agent.get("data"))
    named = _names(data.get("reads")) + _names(data.get("writes"))
    if not named:
        return [f"prompt {name} reads and writes no data location"]
    known = {loc.name for loc in (declaration.locations if declaration else ())}
    return [
        f"prompt {name} names undeclared data location {n}"
        for n in named
        if n not in known
    ]


def _contract_gaps(name: str, agent: Mapping[str, Any]) -> list[str]:
    found = []
    if not _names(_mapping(agent.get("guardrails")).get("never")):
        found.append(f"prompt {name} has no never guardrail")
    if not _names(agent.get("outputs")):
        found.append(f"prompt {name} names no output")
    return found


def prompt_gaps(
    name: str,
    document: Mapping[str, Any],
    declaration: SurfaceDeclaration | None,
    served_tools: set[str],
    skills: set[str],
) -> list[str]:
    """What one prompt document lacks as a complete agent definition."""
    found = _identity_gaps(name, document)
    agent = document.get("agent")
    if not isinstance(agent, Mapping):
        return [*found, f"prompt {name} has no agent definition"]
    found += _kind_gaps(name, agent)
    found += _tool_gaps(name, agent, declaration, served_tools)
    found += _skill_gaps(name, agent, skills)
    found += _data_gaps(name, agent, declaration)
    found += _contract_gaps(name, agent)
    return found


def _role_gaps(
    declaration: SurfaceDeclaration | None, agents: list[Mapping[str, Any]]
) -> list[str]:
    found = []
    if not any(a.get("kind") == "operator" for a in agents):
        found.append("no operator prompt")
    roles = declaration.roles if declaration else ()
    if not roles:
        found.append("no business role declared")
    served_roles = {
        a.get("business_role") for a in agents if a.get("kind") == "business-role"
    }
    found += [
        f"business role {r.name} has no business-role prompt"
        for r in roles
        if r.name not in served_roles
    ]
    return found


def agent_gaps(
    declaration: SurfaceDeclaration | None,
    documents: Mapping[str, Mapping[str, Any]],
    served_tools: set[str],
    skills: set[str],
) -> list[str]:
    """Agent-definition gaps of every prompt plus the connector-level role rules."""
    documents = {n: d for n, d in documents.items() if n not in PLATFORM_PROMPTS}
    found: list[str] = []
    for name in sorted(documents):
        found += prompt_gaps(name, documents[name], declaration, served_tools, skills)
    agents = [_mapping(d.get("agent")) for d in documents.values()]
    return found + _role_gaps(declaration, agents)
