"""Read one declared prompt blueprint file; shared by serving and packing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["ContentError", "composed_text", "prompt_document"]


class ContentError(ValueError):
    """A connector content file is malformed or missing."""


def prompt_document(path: Path) -> dict[str, Any]:
    """The JSON object a prompt file declares."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentError(f"prompt {path.name} is not readable JSON") from exc
    if not isinstance(document, dict):
        raise ContentError(f"prompt {path.name} has no instructions.core_directive")
    return document


def composed_text(path: Path, document: dict[str, Any]) -> str:
    """The prompt body, which lives only in ``instructions.core_directive``."""
    instructions = document.get("instructions")
    directive = (
        instructions.get("core_directive") if isinstance(instructions, dict) else None
    )
    if not isinstance(directive, str) or not directive.strip():
        raise ContentError(f"prompt {path.name} has no instructions.core_directive")
    return directive
