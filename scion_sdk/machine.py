"""Link a connector record to the machine it describes.

One helper builds the ``observes`` relationship. The anchor id of a machine key is derived by the
engine wheel (``medulla.engine.machine_key_ref``), never recomputed here, so every connector
and the engine agree on it byte for byte.
"""

from __future__ import annotations

import importlib
import json
from typing import Literal

from scion_sdk.ingest.model import Relationship
from scion_sdk.manifest.model import ResourceRelation

__all__ = [
    "OBSERVES_RELATION",
    "MachineKeyKind",
    "MachineKeyRefused",
    "machine_key_anchor",
    "observes_machine",
]

MachineKeyKind = Literal["uuid", "serial", "bmc", "host"]

OBSERVES_RELATION = ResourceRelation(
    name="observes", label="observes", target="MachineKey"
).model_dump()


class MachineKeyRefused(ValueError):
    """The engine refused a machine key as ``malformed``, ``placeholder`` or ``empty``."""

    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(f"{kind}:{reason}")
        self.kind = kind
        self.reason = reason


_REASONS = ("malformed", "placeholder", "empty")


def machine_key_anchor(kind: MachineKeyKind, raw: str) -> str:
    """The key anchor id ``urn:machine-key:<kind>:<32 hex>`` of one machine key."""
    engine = importlib.import_module("medulla.engine")

    try:
        answer = engine.machine_key_ref(kind, raw)
    except ValueError as exc:
        reason = str(exc).rpartition(":")[2].strip()
        if reason in _REASONS:
            raise MachineKeyRefused(kind, reason) from exc
        raise
    return str(json.loads(answer)["iri"])


def observes_machine(record_id: str, kind: MachineKeyKind, raw: str) -> Relationship:
    """The ``observes`` relationship from a connector record to a machine key anchor."""
    return Relationship(
        source=record_id,
        target=machine_key_anchor(kind, raw),
        relationship="observes",
    )
