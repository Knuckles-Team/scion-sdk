"""Pinned-engine capability guard.

A method the engine serves on its main branch but the pinned engine client does
not yet carry is refused with a typed :class:`EngineCapabilityUnavailable` that
names the method. Nothing pretends to succeed. Once the pin moves, the guard
passes and the real call runs.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping

__all__ = [
    "ENGINE_CAPABILITIES",
    "EngineCapabilityUnavailable",
    "engine_capability_present",
    "require_engine_capability",
]

# method name -> (module, attribute) the pinned engine client must export.
ENGINE_CAPABILITIES: Mapping[str, tuple[str, str]] = {
    "InstanceProfile": ("medulla.generated.source_ingestion", "InstanceProfile"),
    "ProfileSubmission": ("medulla.generated.source_ingestion", "ProfileSubmission"),
    "SchemaDriftQuarantinedError.drift": ("medulla", "SchemaDriftQuarantinedError"),
}


class EngineCapabilityUnavailable(RuntimeError):
    """The pinned engine client lacks ``method``; the call was not attempted."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"the pinned engine does not serve {method}")


def engine_capability_present(method: str) -> bool:
    """Whether the pinned engine client exports the shape that ``method`` needs."""
    try:
        module_name, attribute = ENGINE_CAPABILITIES[method]
    except KeyError:
        raise ValueError(f"unknown engine capability: {method}") from None
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    return hasattr(module, attribute)


def require_engine_capability(method: str) -> None:
    """Raise :class:`EngineCapabilityUnavailable` while the pin lacks ``method``."""
    if not engine_capability_present(method):
        raise EngineCapabilityUnavailable(method)
