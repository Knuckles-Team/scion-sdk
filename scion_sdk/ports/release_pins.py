"""The pinned release digests a runner verifies a connector against."""

from __future__ import annotations

from typing import Protocol

__all__ = ["ReleasePins"]


class ReleasePins(Protocol):
    """Where the pinned digests of a connector release are read.

    The release pipeline records the digests when it publishes a release; an
    implementation reads them back. The runner never writes a pin and never
    accepts a digest the release did not record.
    """

    async def pinned_digests(self, distribution: str, version: str) -> frozenset[str]:
        """Every digest recorded for the release, or an empty set when none is.

        Digests are strings such as ``sha256:<hex>`` for a wheel or sdist and
        ``content-sha256:<hex>`` for the wheel content digest. An unreachable
        record raises, and the runner refuses the connector.
        """
        ...
