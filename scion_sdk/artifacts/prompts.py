"""Pack entries for served prompts: each body is the prompt's canonical blueprint."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from scion_sdk.artifacts.annotations import _annotations_from_mcp
from scion_sdk.artifacts.common import json_object, require_kind
from scion_sdk.artifacts.prompt_blueprint import (
    blueprint_from_source,
    refusal,
    validate_prompt_blueprint,
)
from scion_sdk.artifacts.prompts_helpers import (
    _bounded_body,
    _definition,
    _prompt_uri,
)
from scion_sdk.contracts import CapturedArtifact, ServerIdentity
from scion_sdk.http.bodies import DEFAULT_MAX_RESPONSE_BYTES
from scion_sdk.ports.errors import MalformedArtifactError
from scion_sdk.ports.session import McpSession

__all__ = ["PromptArtifactKind"]


class PromptArtifactKind:
    """Served prompts packed as canonical prompt blueprints.

    ``sources`` maps each served prompt name to the blueprint file the
    connector declares for it. The listing proves what is served; the body of
    each entry is that file, read by the loader the serving path uses. A served
    prompt with no declared file is a build error naming the prompt. There is
    no capture shape and no synthesized blueprint.
    """

    kind = "prompt"

    def __init__(self, sources: Mapping[str, Path] | None = None) -> None:
        self._sources: dict[str, Path] = dict(sources or {})

    def _existing(self, server: ServerIdentity) -> frozenset[str]:
        return frozenset(_prompt_uri(server, name) for name in self._sources)

    async def list_entries(
        self, session: McpSession, server: ServerIdentity
    ) -> tuple[CapturedArtifact, ...]:
        """One blueprint entry per served prompt, bounded in aggregate.

        Raises:
            MalformedArtifactError: a served prompt has no declared blueprint
                file, or its file is not a canonical blueprint.
        """
        entries: list[CapturedArtifact] = []
        remaining = DEFAULT_MAX_RESPONSE_BYTES
        for listed in await session.list_prompts():
            prompt = _definition(listed)
            source = self._sources.get(prompt.name)
            if source is None:
                raise MalformedArtifactError(
                    f"prompt {prompt.name!r} is served but has no blueprint source"
                )
            document = blueprint_from_source(
                prompt.name,
                source,
                connector=server.name,
                existing=self._existing(server),
            )
            entry = CapturedArtifact(
                kind=self.kind,
                uri=_prompt_uri(server, prompt.name),
                name=prompt.name,
                media_type="application/json",
                body=_bounded_body(document, limit=remaining),
                server=server,
                annotations=_annotations_from_mcp(prompt),
            )
            self.validate(entry)
            remaining -= len(entry.body)
            entries.append(entry)
        return tuple(entries)

    def validate(self, entry: CapturedArtifact) -> None:
        """Require the engine's canonical prompt blueprint, bound to its identity."""
        require_kind(entry, self.kind)
        if len(entry.body.encode("utf-8")) > DEFAULT_MAX_RESPONSE_BYTES:
            raise MalformedArtifactError(
                "MCP prompt entry exceeds the response byte bound"
            )
        document = json_object(entry)
        violations = validate_prompt_blueprint(document, self._existing(entry.server))
        if violations:
            raise refusal(entry.name, violations)
        if entry.uri != _prompt_uri(entry.server, entry.name):
            raise MalformedArtifactError(
                "MCP prompt identity differs from its server and blueprint"
            )
