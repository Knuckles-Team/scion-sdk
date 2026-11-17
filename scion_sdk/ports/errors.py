"""Errors raised through the extension ports."""

from __future__ import annotations

__all__ = [
    "MalformedArtifactError",
    "MalformedSourceDataError",
    "McpTransportError",
    "SourceContractError",
]


class SourceContractError(RuntimeError):
    """The live source does not satisfy its pinned contract, or was not verified."""


class MalformedSourceDataError(ValueError):
    """A source returned data that does not match the declared record shape."""


class MalformedArtifactError(ValueError):
    """An MCP-served content entry is malformed for its kind."""


class McpTransportError(RuntimeError):
    """An MCP operation failed; the message names the operation, not the payload."""
