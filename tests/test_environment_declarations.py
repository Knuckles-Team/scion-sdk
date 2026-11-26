"""The SDK declares every environment name it reads (R908)."""

from __future__ import annotations

from pathlib import Path

import pytest

import scion_sdk
from scion_sdk.environment import SCHEMA_VERSION, EnvironmentDeclaration, declarations
from scion_sdk.mcp._env_drift_reads import _scan_setting_calls


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R908")
def test_every_read_is_declared() -> None:
    declared = {d.name for d in declarations()}
    read = _scan_setting_calls(Path(scion_sdk.__file__).parent)
    assert read - declared == set(), "undeclared environment reads"
    assert SCHEMA_VERSION == 1
    assert all(isinstance(d, EnvironmentDeclaration) for d in declarations())
    names = [d.name for d in declarations()]
    assert names == sorted(set(names))
    assert all(d.call_sites for d in declarations())
