"""Pure helpers for :mod:`scion_sdk.artifacts.prompts`."""

from __future__ import annotations

import json
from typing import Any

import mcp_types
from pydantic import ValidationError

from scion_sdk.contracts import ServerIdentity
from scion_sdk.ports.errors import MalformedArtifactError


def _definition(value: Any) -> mcp_types.Prompt:
    try:
        prompt = mcp_types.Prompt.model_validate(value)
    except ValidationError as exc:
        raise MalformedArtifactError("MCP prompt definition is malformed") from exc
    arguments = prompt.arguments or []
    names = [argument.name for argument in arguments]
    if not prompt.name.strip() or any(not name.strip() for name in names):
        raise MalformedArtifactError("MCP prompt requires nonempty names")
    if len(names) != len(set(names)):
        raise MalformedArtifactError(
            f"prompt {prompt.name!r} declares a duplicate argument name"
        )
    return prompt


def _bounded_body(document: dict[str, Any], *, limit: int) -> str:
    chunks: list[str] = []
    size = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    for chunk in encoder.iterencode(document):
        size += len(chunk)
        if size > limit:
            raise MalformedArtifactError(
                "MCP prompt capture exceeds the response byte bound"
            )
        chunks.append(chunk)
    return "".join(chunks)


def _prompt_uri(server: ServerIdentity, name: str) -> str:
    return f"prompt://{server.name}/{name}"
