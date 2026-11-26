"""Typed observation producer contract.

SCION-CONNECTOR-CONTROL-R910. A connector tool that observes returns
``Observation(source, frame, revision, payload)``; the conformance kit calls
:func:`require_observation` on every observing tool's result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Observation", "ObservationError", "require_observation"]


class ObservationError(ValueError):
    """A tool result is not a typed observation triple."""


class Observation(BaseModel):
    """What was seen (``payload``) at ``revision`` of ``frame`` from ``source``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    frame: str = Field(min_length=1)
    revision: int = Field(ge=0)
    payload: Mapping[str, Any]


def require_observation(result: object) -> Observation:
    """Return ``result`` as an observation; refuse a result missing the triple."""
    if isinstance(result, Observation):
        return result
    try:
        return Observation.model_validate(result)
    except ValueError as error:
        raise ObservationError("observation triple required") from error
