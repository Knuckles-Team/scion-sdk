"""Engine validation of an emitted pack before import (SCION-CONNECTOR-CONTROL-R067.4).

The certification command sends the emitted triples to the engine's ``ShaclValidate``
method with the engine's own connector-pack shapes. The SDK holds no SHACL engine
and an engine refusal reaches the caller with its typed code unchanged.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from medulla.client import RdfClient

__all__ = ["PackPreflightRefused", "preflight_pack_turtle"]


class PackPreflightRefused(ValueError):
    """The engine reported the pack as non-conforming; ``report`` is its report."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("pack preflight refused by the engine shapes")
        self.report = report


def _engine_shapes() -> str:
    """The connector-pack shapes shipped with the engine client distribution."""
    return (
        resources.files("medulla")
        .joinpath("ontology/connector_pack-v1.shapes.ttl")
        .read_text(encoding="utf-8")
    )


async def preflight_pack_turtle(client: RdfClient, turtle: str) -> dict[str, Any]:
    """Validate ``turtle`` inline through the engine; refuse a non-conforming report."""
    report = await client.validate_shacl(_engine_shapes(), turtle)
    if report.get("conforms") is False:
        raise PackPreflightRefused(report)
    return report
