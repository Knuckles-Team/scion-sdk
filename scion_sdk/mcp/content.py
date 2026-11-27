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

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from fastmcp.resources import FileResource, TextResource
from fastmcp.server.providers.skills import SkillsDirectoryProvider
from pydantic import AnyUrl

from scion_sdk.manifest.prompt_source import prompt_document
from scion_sdk.manifest.release_digest import (
    ReleaseDigestError,
    ReleaseEvidence,
    installed_release_evidence,
)
from scion_sdk.manifest.seed_graph import SeedGraphDeclaration, SeedTriple
from scion_sdk.mcp._content_descriptors import resource_descriptor_source
from scion_sdk.mcp._content_prompts import (
    PROMPT_META_KEY,
    _register_prompt,
)
from scion_sdk.mcp.prompt_pack_meta import ContentError

__all__ = [
    "DIGEST_META_KEY",
    "PROMPT_META_KEY",
    "RELEASE_URI",
    "SEED_RESOURCE_URI",
    "ConnectorContent",
    "ContentError",
    "ContentRegistration",
    "contracts_resource",
    "contracts_resources",
    "install_pending_seed",
    "ontology_resources",
    "refresh_seed_resource",
    "register_connector_content",
    "register_ontology_resources",
    "register_release_resource",
    "register_seed_resource",
    "seed_declaration",
]

#: The wheel content digest resource (SCION-CONNECTOR-CONTROL-R650.1.1).
RELEASE_URI = "connector://release"


#: The seed graph resource of a connector (R068.1).
SEED_RESOURCE_URI = "seed://{connector}/seed.ttl"


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
    seed: bool = False


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
        path.stem: prompt_document(path)
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
        path.stem: prompt_document(path)
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


def register_release_resource(
    mcp: Any,
    content: ConnectorContent,
    *,
    evidence: Callable[[str], ReleaseEvidence] = installed_release_evidence,
) -> bool:
    """Serve the installed wheel's content digest as ``connector://release``.

    The digest is computed from the installed distribution on every server
    start, never read from a file shipped inside the wheel. A distribution that
    is not installed (a source checkout) serves nothing, and the runner refuses it.
    """
    try:
        report = evidence(_distribution_name(content))
    except ReleaseDigestError:
        return False
    mcp.add_resource(
        TextResource(
            uri=AnyUrl(RELEASE_URI),
            name=RELEASE_URI,
            text=json.dumps(
                {
                    "distribution": report.distribution,
                    "version": report.version,
                    "content_sha256": report.content_sha256,
                },
                sort_keys=True,
            ),
            mime_type="application/json",
        )
    )
    return True


def _distribution_name(content: ConnectorContent) -> str:
    """The distribution that ships the package, from the import name."""
    from importlib.metadata import packages_distributions

    found = packages_distributions().get(content.package_root.name, [])
    return found[0] if found else content.connector


def register_connector_content(
    mcp: Any,
    content: ConnectorContent,
    *,
    release_evidence: Callable[[str], ReleaseEvidence] = installed_release_evidence,
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
    source = resource_descriptor_source(mcp, content)
    seed = source is not None and register_seed_resource(mcp, content, source)
    register_release_resource(mcp, content, evidence=release_evidence)
    return ContentRegistration(
        skills=skill_count,
        prompts=len(prompt_paths),
        resources=len(resources),
        data_resources=0 if source is None else 1,
        seed=seed,
    )


#: Metadata key carrying a served contract resource's SHA-256 digest (R068.5).
DIGEST_META_KEY = "scion/digest"
_SEED_NS = "urn:scion:seed:"
_SEED_DIGESTS: WeakKeyDictionary[Any, str] = WeakKeyDictionary()
_SEED_SOURCES: WeakKeyDictionary[
    Any, tuple[ConnectorContent, Callable[[], list[dict[str, Any]]]]
] = WeakKeyDictionary()


def seed_declaration(
    content: ConnectorContent, descriptors: list[dict[str, Any]]
) -> SeedGraphDeclaration:
    """The seed of one connector: identity, object catalog and references only."""
    instance = f"seed://{content.connector}/instance"
    identity = (
        SeedTriple(instance, f"{_SEED_NS}connector", content.connector, literal=True),
        SeedTriple(
            instance, f"{_SEED_NS}version", content.package_version, literal=True
        ),
    )
    catalog: list[SeedTriple] = []
    references: list[SeedTriple] = []
    for descriptor in descriptors:
        resource = f"seed://{content.connector}/resource/{descriptor['name']}"
        catalog.append(SeedTriple(instance, f"{_SEED_NS}hasResource", resource))
        if descriptor.get("ontology_class"):
            references.append(
                SeedTriple(
                    resource, f"{_SEED_NS}class", str(descriptor["ontology_class"])
                )
            )
    return SeedGraphDeclaration(instance, identity, tuple(catalog), tuple(references))


def _seed_resource(
    content: ConnectorContent, descriptors: list[dict[str, Any]]
) -> tuple[TextResource, str]:
    from scion_sdk.manifest.seed_graph import emit_seed_turtle

    emitted = emit_seed_turtle(seed_declaration(content, descriptors))
    uri = SEED_RESOURCE_URI.format(connector=content.connector)
    resource = TextResource(
        uri=AnyUrl(uri),
        name=uri,
        text=emitted.turtle,
        mime_type="text/turtle",
        meta={DIGEST_META_KEY: emitted.digest},
    )
    return resource, emitted.digest


def register_seed_resource(
    mcp: Any, content: ConnectorContent, source: Callable[[], list[dict[str, Any]]]
) -> bool:
    """Serve ``seed://<connector>/seed.ttl`` from the live descriptors (R068.3).

    Content registers before the connector's tools. While a declared tool is not
    registered yet the descriptors cannot be built, so the source is remembered and
    ``install_pending_seed`` serves the seed once the tool surface is registered.
    Returns whether the seed is served now.
    """
    _SEED_SOURCES[mcp] = (content, source)
    return _install_seed(mcp)


def _install_seed(mcp: Any) -> bool:
    content, source = _SEED_SOURCES[mcp]
    try:
        descriptors = source()
    except ValueError:  # a declared tool is not registered yet
        return False
    resource, digest = _seed_resource(content, descriptors)
    mcp.add_resource(resource)
    _SEED_DIGESTS[mcp] = digest
    return True


def install_pending_seed(mcp: Any) -> bool:
    """Serve the seed after the tool surface registers, when content deferred it."""
    if mcp not in _SEED_SOURCES or mcp in _SEED_DIGESTS:
        return False
    return _install_seed(mcp)


async def refresh_seed_resource(mcp: Any) -> bool:
    """Re-serve the seed; send one ``list_changed`` when its digest changed.

    Raises:
        LookupError: the server serves no seed resource.
    """
    from scion_sdk.mcp.change_events import announce_resources_changed

    registered = _SEED_SOURCES.get(mcp)
    if registered is None:
        raise LookupError("this server serves no seed resource")
    content, source = registered
    resource, digest = _seed_resource(content, source())
    if _SEED_DIGESTS.get(mcp) == digest:
        return False
    mcp.add_resource(resource)
    _SEED_DIGESTS[mcp] = digest
    await announce_resources_changed(mcp)
    return True
