"""The read-only ``source_access_check`` tool (SCION-SOURCE-INGEST-R805.5).

A source that declares ``access_mode: checked_at_read`` answers the engine's
query-time access lookups through this tool. It fails closed: no source with
that mode, or no provider for one, refuses registration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from scion_sdk.manifest.sources import IngestSourceSpec
from scion_sdk.ports.access_check import AccessCheckPort, BoundedAccessCheck

__all__ = [
    "ACCESS_CHECK_PROVIDER_MISSING",
    "ACCESS_MODE_UNDECLARED",
    "AccessCheckToolRefused",
    "register_access_check_tool",
]

ACCESS_MODE_UNDECLARED = "ACCESS_MODE_UNDECLARED"
ACCESS_CHECK_PROVIDER_MISSING = "ACCESS_CHECK_PROVIDER_MISSING"


class AccessCheckToolRefused(ValueError):
    """Registration refused; ``code`` is closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def register_access_check_tool(
    server: Any,
    provider: AccessCheckPort | None,
    sources: Sequence[IngestSourceSpec],
) -> None:
    """Register ``source_access_check``; refuse when the declaration or provider is absent."""
    if not any(source.access_mode == "checked_at_read" for source in sources):
        raise AccessCheckToolRefused(ACCESS_MODE_UNDECLARED)
    if provider is None:
        raise AccessCheckToolRefused(ACCESS_CHECK_PROVIDER_MISSING)
    bounded = BoundedAccessCheck(provider)

    async def source_access_check(
        principal: str, record_refs: list[str]
    ) -> dict[str, Any]:
        """Partition record references into allowed and denied for one principal."""
        answer = await bounded.check(principal, record_refs)
        return {
            "allowed": list(answer.allowed),
            "denied": list(answer.denied),
            "checked_at": answer.checked_at.isoformat(),
        }

    server.tool(name="source_access_check", annotations={"readOnlyHint": True})(
        source_access_check
    )
