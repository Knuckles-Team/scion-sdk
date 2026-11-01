"""Shared constants and helpers for the governed write-back modules."""

from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any

#: the single change-set field that carries a sink's operations.
PATCH_FIELD = "ops"


def _write_back() -> Any:
    """Import the engine write-back model module on first use.

    The engine is optional at import time, so collection works without it.
    """
    return importlib.import_module("medulla.generated.write_back")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()
