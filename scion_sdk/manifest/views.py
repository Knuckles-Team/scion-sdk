"""Generated views of the pack declaration (SCION-CONNECTOR-CONTROL-R067.3, .7).

The mapping view and the certification record are derived from the one
declaration; certification compares the view on disk with a fresh render and fails
on any difference.
"""

from __future__ import annotations

import json
from pathlib import Path

from scion_sdk.manifest.pack_declaration import ConnectorPackDeclaration
from scion_sdk.manifest.pack_turtle import declaration_digest

__all__ = [
    "CERTIFICATION_FILE",
    "MAPPING_VIEW_FILE",
    "MappingViewDrift",
    "check_mapping_view",
    "read_certification_record",
    "render_mapping_view",
    "write_certification_record",
    "write_mapping_view",
]

MAPPING_VIEW_FILE = Path("ontology") / "mappings" / "source.yaml"
CERTIFICATION_FILE = Path("ontology") / "certification.json"


class MappingViewDrift(ValueError):
    """The mapping view on disk differs from a fresh render of the declaration."""

    code = "view_drift"


def render_mapping_view(declaration: ConnectorPackDeclaration) -> str:
    """Deterministic YAML of the sources and field mappings (a generated view)."""
    scalar = json.dumps
    lines = [
        "# Generated from the connector pack declaration. Do not edit.",
        f"connector: {scalar(declaration.connector)}",
        f"declaration_digest: {scalar(declaration_digest(declaration))}",
        "sources:" if declaration.sources else "sources: []",
    ]
    for source in sorted(declaration.sources, key=lambda s: s.name):
        lines += [
            f"  - name: {scalar(source.name)}",
            f"    class: {scalar(source.class_iri)}",
            f"    key_fields: {scalar(list(source.key_fields))}",
            "    fields:" if source.field_mappings else "    fields: []",
        ]
        for mapping in sorted(source.field_mappings, key=lambda m: m.field):
            lines.append(
                f"      - {{field: {scalar(mapping.field)}, "
                f"property: {scalar(mapping.property_iri)}}}"
            )
    return "\n".join(lines) + "\n"


def write_mapping_view(
    package_root: Path, declaration: ConnectorPackDeclaration
) -> Path:
    """Write the generated view under ``<package root>/ontology/mappings/source.yaml``."""
    target = package_root / MAPPING_VIEW_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_mapping_view(declaration), encoding="utf-8")
    return target


def check_mapping_view(
    package_root: Path, declaration: ConnectorPackDeclaration
) -> None:
    """Fail on a missing or hand-edited view."""
    target = package_root / MAPPING_VIEW_FILE
    if not target.is_file() or target.read_text(
        encoding="utf-8"
    ) != render_mapping_view(declaration):
        raise MappingViewDrift(f"{MAPPING_VIEW_FILE} differs from the declaration")


def write_certification_record(
    package_root: Path, declaration: ConnectorPackDeclaration, accepted_digest: str
) -> Path:
    """Record the contract digest the engine accepted beside the declaration digest."""
    if len(accepted_digest) != 64 or accepted_digest != accepted_digest.lower():
        raise ValueError("the accepted contract digest must be 64 lowercase hex")
    target = package_root / CERTIFICATION_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "connector": declaration.connector,
                "contract_digest": accepted_digest,
                "declaration_digest": declaration_digest(declaration),
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def read_certification_record(package_root: Path) -> dict[str, str]:
    """The stored certification record."""
    document = json.loads(
        (package_root / CERTIFICATION_FILE).read_text(encoding="utf-8")
    )
    return {str(key): str(value) for key, value in document.items()}
