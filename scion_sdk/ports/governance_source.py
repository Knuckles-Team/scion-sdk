"""The source-governance port: access, groups, identifiers and containers.

SCION-SOURCE-INGEST-R805.4. The runner reads these four facts from a source and
reports them to the engine as typed batches; the engine admits them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from scion_sdk.contracts import ExternalAccess

__all__ = ["GovernanceSource"]


class GovernanceSource(Protocol):
    """What ``sync_governance`` reads from one source."""

    source: str

    def fetch_access(self) -> Iterator[tuple[str, ExternalAccess]]: ...

    def fetch_groups(self) -> Iterator[object]: ...

    def slim_ids(self) -> Iterator[str]: ...

    def containers(self) -> Iterator[object]: ...
