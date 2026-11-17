"""The canonical prompt blueprint a connector pack carries for every prompt.

The engine validates each imported prompt against one fixed shape
(MEDULLA-DECISION-ENGINE-R814.8). This module mirrors those rules field by
field, with the same reason strings, so a pack is refused at build time rather
than at import. A violation names a field path and a rule, never a value.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scion_sdk.manifest.prompt_source import (
    ContentError,
    composed_text,
    prompt_document,
)
from scion_sdk.ports.errors import MalformedArtifactError

__all__ = [
    "BLUEPRINT_SCHEMA_VERSION",
    "BlueprintViolation",
    "blueprint_from_source",
    "validate_prompt_blueprint",
]

#: The only blueprint schema version.
BLUEPRINT_SCHEMA_VERSION = "1.0"
_COMPOSE = ("append", "prepend", "replace")
_MAX_SLUG = 128


@dataclass(frozen=True)
class BlueprintViolation:
    """One broken rule at one field path."""

    path: str
    rule: str


def _is_slug(text: str) -> bool:
    return (
        0 < len(text) <= _MAX_SLUG
        and not text.startswith("-")
        and not text.endswith("-")
        and all(c.isascii() and (c.islower() or c.isdigit() or c == "-") for c in text)
    )


def _text(document: dict[str, Any], key: str) -> str | None:
    value = document.get(key)
    return value if isinstance(value, str) else None


def validate_prompt_blueprint(
    document: object, existing: Collection[str] = ()
) -> list[BlueprintViolation]:
    """Every violation of the canonical prompt blueprint, in the engine's order.

    ``existing`` holds the URIs of the prompts a blueprint may ``extends``.
    """
    if not isinstance(document, dict):
        return [BlueprintViolation("$", "must_be_object")]
    found: list[BlueprintViolation] = []
    task = _text(document, "task")
    source = _text(document, "source")
    if document.get("schema_version") != BLUEPRINT_SCHEMA_VERSION:
        found.append(BlueprintViolation("schema_version", "must_be_1_0"))
    if task is None or not _is_slug(task):
        found.append(BlueprintViolation("task", "must_be_slug"))
    if document.get("type") != "prompt":
        found.append(BlueprintViolation("type", "must_be_prompt"))
    if source is None or not source.strip():
        found.append(BlueprintViolation("source", "must_name_provenance"))
    found.extend(
        BlueprintViolation(key, "body_only_in_core_directive")
        for key in ("content", "input")
        if key in document
    )
    instructions = document.get("instructions")
    body = (
        instructions.get("core_directive") if isinstance(instructions, dict) else None
    )
    if not isinstance(body, str) or not body.strip():
        found.append(
            BlueprintViolation("instructions.core_directive", "must_hold_the_body")
        )
    if "extends" in document and document["extends"] not in existing:
        found.append(BlueprintViolation("extends", "must_name_an_existing_prompt"))
    if "compose" in document and document["compose"] not in _COMPOSE:
        found.append(BlueprintViolation("compose", "must_be_append_prepend_or_replace"))
    return found


def refusal(name: str, violations: list[BlueprintViolation]) -> MalformedArtifactError:
    """The build error for a prompt that is not a canonical blueprint."""
    listed = "; ".join(f"{v.path} ({v.rule})" for v in violations)
    return MalformedArtifactError(
        f"prompt {name!r} is not a canonical prompt blueprint: {listed}"
    )


def blueprint_from_source(
    name: str, path: Path, *, connector: str, existing: Collection[str]
) -> dict[str, Any]:
    """The pack entry document for one served prompt: its declared blueprint file.

    The file is read by the loader the serving path uses, so a prompt that is
    served is read the same way it is packed. The envelope fields the file
    leaves out are the provenance the builder knows: the schema version, the
    prompt slug, the type, and the connector plus the source file name. The
    body is never produced here; a file with no ``instructions.core_directive``
    is refused.

    Raises:
        MalformedArtifactError: the file is unreadable or is not a blueprint.
    """
    try:
        document = dict(prompt_document(path))
        composed_text(path, document)
    except ContentError as exc:
        raise MalformedArtifactError(f"prompt {name!r}: {exc}") from exc
    document.setdefault("schema_version", BLUEPRINT_SCHEMA_VERSION)
    document.setdefault("task", name)
    document.setdefault("type", "prompt")
    document.setdefault("source", f"{connector}/prompts/{path.name}")
    violations = validate_prompt_blueprint(document, existing)
    if violations:
        raise refusal(name, violations)
    return document
