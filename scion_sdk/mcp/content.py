"""Serve a connector's declarative content as native MCP primitives.

RF-ADR-009 section 2.1: skills, prompts, ontologies, SHACL shapes and the
data-source declaration reach medulla through the MCP primitives the server
already serves. Registered from the connector package layout:

==================================  ==============================================
Package path                        Served as
==================================  ==============================================
``skills/<name>/SKILL.md``          ``skill://<name>/SKILL.md`` (skills provider)
``prompts/<name>.json``             MCP prompt ``<name>`` (``prompts/list``)
``ontology/<file>.ttl``             ``ontology://<connector>/<file>.ttl``
``ontology/shapes/<file>.ttl``      ``shapes://<connector>/<file>.ttl``
``connector_contracts.py``          ``ontology://<connector>/contracts.ttl``
(tool ``meta`` via ``source_meta``)   ``connector://resources``
==================================  ==============================================

Replaces ``_register_skill_providers`` and ``_register_prompt_providers`` in
``agent_utilities.mcp.server_factory``, which resolved providers through AU's
install state and served prompts as ``prompt://`` resources rather than MCP
prompts. A malformed content file is an error, not a skipped warning: serving
less than the package declares is how provisioning drifts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp.resources import FileResource, TextResource
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from pydantic import AnyUrl

from scion_sdk.mcp._content_descriptors import _register_resource_descriptors
from scion_sdk.mcp._content_prompts import (
    PROMPT_META_KEY,
    _prompt_document,
    _register_prompt,
)
from scion_sdk.mcp.prompt_pack_meta import ContentError

__all__ = [
    "PROMPT_META_KEY",
    "ConnectorContent",
    "ContentError",
    "ContentRegistration",
    "contracts_resource",
    "contracts_resources",
    "ontology_resources",
    "register_connector_content",
    "register_ontology_resources",
]


@dataclass(frozen=True)
class ConnectorContent:
    """Where a connector's content lives.

    Args:
        connector: The connector package name used in resource URIs.
        package_root: The package directory holding ``skills/``, ``prompts/``
            and ``ontology/``.
        package_version: The publisher version recorded on ConnectorPack import.
    """

    connector: str
    package_root: Path
    package_version: str

    def __post_init__(self) -> None:
        if not self.connector.strip() or not self.package_version.strip():
            raise ValueError("connector and package_version are required")

    @classmethod
    def for_package(
        cls,
        package: str,
        package_root: Path,
        package_version: str,
    ) -> ConnectorContent:
        """Build the content of ``package`` under its declared fleet name.

        The connector name comes from
        :func:`scion_sdk.connector_identity.connector_name`; a connector passes
        no name literal.
        """
        from scion_sdk.connector_identity import connector_name

        return cls(
            connector=connector_name(package),
            package_root=package_root,
            package_version=package_version,
        )


@dataclass(frozen=True)
class ContentRegistration:
    """Counts of what was registered."""

    skills: int
    prompts: int
    resources: int
    data_resources: int = 0


def _register_skills(mcp: Any, skills_root: Path) -> int:
    count = sum(1 for _ in skills_root.glob("*/SKILL.md"))
    if count:
        mcp.add_provider(SkillsDirectoryProvider(skills_root))
    return count


def _file_resource(uri: str, path: Path, mime_type: str) -> FileResource:
    return FileResource(
        uri=AnyUrl(uri), path=path.resolve(), name=uri, mime_type=mime_type
    )


def ontology_resources(connector: str, ontology_dir: Path) -> list[FileResource]:
    """Build the ``ontology://`` and ``shapes://`` resources of one ontology directory.

    ``ontology_dir/*.ttl`` become ``ontology://<connector>/<file>.ttl`` and
    ``ontology_dir/shapes/*.ttl`` become ``shapes://<connector>/<file>.ttl``.
    A missing directory yields no resources.
    """
    return [
        _file_resource(f"{scheme}://{connector}/{path.name}", path, "text/turtle")
        for scheme, directory in (
            ("ontology", ontology_dir),
            ("shapes", ontology_dir / "shapes"),
        )
        for path in sorted(directory.glob("*.ttl"))
    ]


def register_ontology_resources(mcp: Any, connector: str, ontology_dir: Path) -> int:
    """Register one ontology directory's resources on ``mcp``; return the count.

    The host-side half of :func:`register_connector_content` for servers that
    already serve skills and prompts through their own providers.
    """
    resources = ontology_resources(connector, ontology_dir)
    for resource in resources:
        mcp.add_resource(resource)
    return len(resources)


def contracts_resource(content: ConnectorContent) -> TextResource | None:
    """The rendered surface contracts as ``ontology://<connector>/contracts.ttl``.

    ``None`` when the package declares no ``connector_contracts`` module
    (SCION-CONNECTOR-CONTROL-R916.3).
    """
    from scion_sdk.surface_contracts import load_surface, render_surface_turtle

    if not (content.package_root / "connector_contracts.py").is_file():
        return None
    declaration = load_surface(content.package_root.name, connector=content.connector)
    if declaration is None:
        return None
    uri = f"ontology://{content.connector}/contracts.ttl"
    prompts = {
        path.stem: _prompt_document(path)
        for path in sorted((content.package_root / "prompts").glob("*.json"))
        if not path.name.startswith("_")
    }
    return TextResource(
        uri=AnyUrl(uri),
        name=uri,
        text=render_surface_turtle(declaration, prompts),
        mime_type="text/turtle",
    )


def contracts_resources(content: ConnectorContent) -> list[TextResource]:
    """The surface contracts as ``contracts.ttl`` and, when one body would pass the pack bound, ``contracts-2.ttl`` ...

    A connector pack bounds each body at 2 MiB, so a connector with hundreds of contracts is served in
    self-contained shards cut at subject boundaries (SCION-CONNECTOR-CONTROL-R916.3).
    """
    from scion_sdk.surface_contracts import load_surface, render_surface_turtle_shards

    if not (content.package_root / "connector_contracts.py").is_file():
        return []
    declaration = load_surface(content.package_root.name, connector=content.connector)
    if declaration is None:
        return []
    prompts = {
        path.stem: _prompt_document(path)
        for path in sorted((content.package_root / "prompts").glob("*.json"))
        if not path.name.startswith("_")
    }
    shards = render_surface_turtle_shards(declaration, prompts)
    out: list[TextResource] = []
    for index, text in enumerate(shards, start=1):
        name = "contracts.ttl" if index == 1 else f"contracts-{index}.ttl"
        uri = f"ontology://{content.connector}/{name}"
        out.append(
            TextResource(uri=AnyUrl(uri), name=uri, text=text, mime_type="text/turtle")
        )
    return out


def _content_resources(content: ConnectorContent) -> list[FileResource | TextResource]:
    found: list[FileResource | TextResource] = list(
        ontology_resources(content.connector, content.package_root / "ontology")
    )
    found.extend(contracts_resources(content))
    return found


def register_connector_content(
    mcp: Any, content: ConnectorContent
) -> ContentRegistration:
    """Register the connector's skills, prompts and content resources on ``mcp``.

    Raises:
        ContentError: a prompt is malformed.
    """
    skill_count = _register_skills(mcp, content.package_root / "skills")
    prompt_paths = sorted(
        path
        for path in (content.package_root / "prompts").glob("*.json")
        if not path.name.startswith("_")
    )
    for path in prompt_paths:
        _register_prompt(mcp, path)
    resources = _content_resources(content)
    for resource in resources:
        mcp.add_resource(resource)
    descriptors = _register_resource_descriptors(mcp, content)
    return ContentRegistration(
        skills=skill_count,
        prompts=len(prompt_paths),
        resources=len(resources),
        data_resources=descriptors,
    )
