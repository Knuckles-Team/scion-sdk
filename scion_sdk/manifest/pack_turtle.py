"""Turtle for one connector pack, emitted from its typed declaration.

SCION-CONNECTOR-CONTROL-R067.2. The output uses only the engine connector-pack
vocabulary (``cp:``) and its surface terms, with absolute IRIs, no blank nodes and
sorted lines, so the same declaration always yields the same bytes. The engine owns
the shapes; the SDK holds no SHACL engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

from scion_sdk.manifest.pack_declaration import (
    ConnectorPackDeclaration,
    DeclaredNode,
    SourceObject,
)

__all__ = [
    "CP",
    "PackDeclarationRefusal",
    "check_pack_declaration",
    "declaration_digest",
    "emit_pack_turtle",
    "load_pack_declaration",
]

CP = "https://knuckles-team.github.io/ontology/connector-pack#"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_ABSOLUTE_IRI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"]+\Z")
_CURSOR_KIND = {"cursor": "opaque", "page": "page_token", "offset": "offset"}
_PROVENANCE_FIELDS = ("record_id", "source_uri")
_NODE_CLASS = {
    "feature": "SurfaceFeature",
    "tool": "SurfaceTool",
    "prompt": "SurfacePrompt",
    "skill": "SurfaceSkill",
}


class PackDeclarationRefusal(ValueError):
    """A closed declaration refusal that names the offending field."""

    CODES = (
        "unresolved_class",
        "nested_property",
        "duplicate_mapping",
        "missing_key_rule",
    )

    def __init__(self, code: str, field: str) -> None:
        if code not in self.CODES:
            raise ValueError(f"unknown refusal code {code}")
        super().__init__(f"{code}: {field}")
        self.code = code
        self.field = field


def load_pack_declaration(document: object) -> ConnectorPackDeclaration:
    """Parse and check a declaration, refusing with a closed code and the field."""
    from pydantic import ValidationError

    try:
        declaration = ConnectorPackDeclaration.model_validate(document)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        if first["type"] == "extra_forbidden":
            raise PackDeclarationRefusal("nested_property", field) from exc
        raise
    check_pack_declaration(declaration)
    return declaration


def check_pack_declaration(declaration: ConnectorPackDeclaration) -> None:
    """Refuse an unresolved class, a duplicate mapping or a missing key rule."""
    for node in declaration.nodes:
        if not _ABSOLUTE_IRI.fullmatch(node.domain_class_iri):
            raise PackDeclarationRefusal("unresolved_class", f"nodes.{node.name}")
    for source in declaration.sources:
        if not _ABSOLUTE_IRI.fullmatch(source.class_iri):
            raise PackDeclarationRefusal("unresolved_class", f"sources.{source.name}")
        fields = [m.field for m in source.field_mappings]
        properties = [m.property_iri for m in source.field_mappings]
        for values, label in ((fields, "field"), (properties, "property_iri")):
            duplicate = next((v for v in values if values.count(v) > 1), None)
            if duplicate is not None:
                raise PackDeclarationRefusal(
                    "duplicate_mapping", f"sources.{source.name}.{label}"
                )
        if source.field_mappings and not source.key_fields:
            raise PackDeclarationRefusal("missing_key_rule", f"sources.{source.name}")
        bad = next(
            (
                m
                for m in source.field_mappings
                if not _ABSOLUTE_IRI.fullmatch(m.property_iri)
            ),
            None,
        )
        if bad is not None:
            raise PackDeclarationRefusal(
                "unresolved_class", f"sources.{source.name}.{bad.field}"
            )


def declaration_digest(declaration: ConnectorPackDeclaration) -> str:
    """SHA-256 (64 lowercase hex) of the declaration's canonical JSON."""
    body = json.dumps(
        declaration.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _lit(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _iri(value: str) -> str:
    return f"<{value}>"


def _local(connector: str, *parts: str) -> str:
    from urllib.parse import quote

    return f"contract://{connector}#" + "/".join(quote(p, safe="") for p in parts)


def _node_iri(connector: str, node: DeclaredNode) -> str:
    if node.kind == "feature":
        return _local(connector, "feature", node.name)
    return f"{node.kind}://{connector}/{node.name}"


def emit_pack_turtle(declaration: ConnectorPackDeclaration) -> str:
    """Sorted Turtle of the contract and surface of ``declaration``.

    Raises:
        PackDeclarationRefusal: the declaration fails :func:`check_pack_declaration`.
    """
    check_pack_declaration(declaration)
    c = declaration.connector
    triples: list[tuple[str, str, str]] = []

    def add(subject: str, predicate: str, obj: str) -> None:
        triples.append((_iri(subject), _iri(predicate), obj))

    def typed(subject: str, kind: str) -> None:
        add(subject, _RDF_TYPE, _iri(CP + kind))

    server = f"mcp-server://{c}"
    contract = _local(c, "contract")
    typed(contract, "IngestContract")
    add(contract, CP + "contractOf", _iri(server))
    add(server, CP + "hasContract", _iri(contract))
    add(contract, CP + "contractVersion", _lit(declaration.contract_version))
    add(contract, CP + "contractDigest", _lit(declaration_digest(declaration)))
    _emit_policies(declaration, contract, add, typed)
    for source in declaration.sources:
        _emit_source(c, contract, source, add, typed)
    _emit_surface(declaration, server, add, typed)
    return "".join(f"{s} {p} {o} .\n" for s, p, o in sorted(set(triples)))


def _emit_policies(declaration, contract, add, typed) -> None:  # type: ignore[no-untyped-def]
    c = declaration.connector
    privacy = sorted({p for s in declaration.sources for p in s.privacy_classes})
    for value in privacy:
        add(contract, CP + "privacyClass", _lit(value))
    cursors = {s.cursor.mode for s in declaration.sources} - {"none"}
    for mode in sorted(cursors):
        cursor = _local(c, "cursor", mode)
        typed(cursor, "CursorPolicy")
        add(cursor, CP + "cursorKind", _lit(_CURSOR_KIND[mode]))
        add(contract, CP + "hasCursorPolicy", _iri(cursor))
    access = _local(c, "access")
    typed(access, "IngestAccessPolicy")
    add(access, CP + "defaultAcl", _lit("quarantine"))
    add(contract, CP + "hasAccessPolicy", _iri(access))
    if any(s.provenance_required for s in declaration.sources):
        provenance = _local(c, "provenance")
        typed(provenance, "ProvenanceRequirement")
        for name in _PROVENANCE_FIELDS:
            add(provenance, CP + "requiredField", _lit(name))
        add(contract, CP + "requiresProvenance", _iri(provenance))


def _emit_source(connector, contract, source: SourceObject, add, typed) -> None:  # type: ignore[no-untyped-def]
    node = _local(connector, "source", source.name)
    typed(node, "SourceObjectMapping")
    add(contract, CP + "hasSourceObject", _iri(node))
    add(node, CP + "objectName", _lit(source.name))
    add(node, CP + "ontologyClass", _iri(source.class_iri))
    if source.key_fields:
        key = _local(connector, "key", source.name)
        typed(key, "KeyRule")
        for field in source.key_fields:
            add(key, CP + "keyField", _lit(field))
        add(node, CP + "hasKeyRule", _iri(key))
    for mapping in source.field_mappings:
        fm = _local(connector, "field", source.name, mapping.field)
        typed(fm, "FieldMapping")
        add(fm, CP + "sourceField", _lit(mapping.field))
        add(fm, CP + "mapsPredicate", _lit(mapping.property_iri))
        add(node, CP + "hasFieldMapping", _iri(fm))


def _emit_surface(declaration, server, add, typed) -> None:  # type: ignore[no-untyped-def]
    c = declaration.connector
    nodes: Iterable[DeclaredNode] = declaration.nodes
    by_class: dict[str, list[DeclaredNode]] = {}
    for node in nodes:
        if node.kind != "feature":
            by_class.setdefault(node.domain_class_iri, []).append(node)
    for node in nodes:
        iri = _node_iri(c, node)
        typed(iri, _NODE_CLASS[node.kind])
        if node.kind == "feature":
            add(server, CP + "providesFeature", _iri(iri))
            for member in by_class.get(node.domain_class_iri, []):
                add(iri, CP + "realizedBy", _iri(_node_iri(c, member)))
        elif node.kind == "tool":
            add(iri, CP + "operatesOn", _iri(node.domain_class_iri))
        else:
            add(iri, CP + "appliesTo", _iri(node.domain_class_iri))
