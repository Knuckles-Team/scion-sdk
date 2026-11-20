"""Explicit fixture serving mode for any connector (SCION-CONNECTOR-CONTROL-R931).

A connector started with ``--fixture-mode`` (or ``SCION_FIXTURE_MODE=true``) lists
its full declared surface but answers each tool call from a recorded fixture in
its package ``fixtures/`` directory. The call never reaches the tool body, so no
backend client is used, no network connection is opened and no credential is
read. A call with no recorded fixture is refused with ``fixture_not_found``; no
response is invented. The mode is never implied by a missing credential, a
failed backend call or a test environment.

A fixture file is JSON: a list of ``{"tool", "arguments", "result"}`` entries.
``result`` carries ``text`` and/or ``structured``. Entries are keyed by tool name
and the digest of the canonical JSON of the arguments.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import TextContent

__all__ = [
    "FIXTURE_DIRECTORY",
    "FixtureMiddleware",
    "FixtureNotFound",
    "FixtureStore",
    "argument_digest",
]

FIXTURE_DIRECTORY = "fixtures"
_REFUSAL_CODE = -32000


class FixtureNotFound(Exception):
    """No recorded fixture matches the call; the typed refusal ``fixture_not_found``."""

    code = _REFUSAL_CODE
    reason = "fixture_not_found"


def argument_digest(arguments: Mapping[str, Any] | None) -> str:
    """The sha256 of the canonical JSON of the arguments (sorted keys, no spaces)."""
    canonical = json.dumps(
        dict(arguments or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FixtureStore:
    """Recorded answers keyed by tool name and canonical argument digest."""

    def __init__(self, answers: Mapping[tuple[str, str], Mapping[str, Any]]) -> None:
        self._answers = dict(answers)

    @classmethod
    def load(cls, directory: Path) -> FixtureStore:
        """Read every ``*.json`` fixture file; a malformed file refuses to load."""
        answers: dict[tuple[str, str], Mapping[str, Any]] = {}
        for path in sorted(directory.glob("*.json")):
            entries = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError(f"{path.name}: a fixture file is a list of entries")
            for entry in entries:
                tool, result = entry.get("tool"), entry.get("result")
                if not isinstance(tool, str) or not isinstance(result, dict):
                    raise ValueError(f"{path.name}: an entry needs a tool and a result")
                answers[(tool, argument_digest(entry.get("arguments")))] = result
        return cls(answers)

    def __len__(self) -> int:
        return len(self._answers)

    def answer(self, tool: str, arguments: Mapping[str, Any] | None) -> ToolResult:
        """The recorded result, flagged as a fixture, or ``FixtureNotFound``."""
        recorded = self._answers.get((tool, argument_digest(arguments)))
        if recorded is None:
            raise FixtureNotFound(
                f"no recorded fixture for {tool} with these arguments"
            )
        text = recorded.get("text")
        structured = recorded.get("structured")
        if text is None and structured is not None:
            text = json.dumps(structured, sort_keys=True)
        return ToolResult(
            content=[TextContent(type="text", text=str(text or ""))],
            structured_content=structured,
            meta={"fixture_mode": True},
        )


class FixtureMiddleware(Middleware):
    """Answer tool calls from the store; the tool body and its backend never run."""

    def __init__(self, store: FixtureStore) -> None:
        self._store = store

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> ToolResult:
        message = context.message
        return self._store.answer(message.name, message.arguments)
