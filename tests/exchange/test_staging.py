"""SCION-CENTRALIZED-CAPABILITIES-R043.2: per-run staging directories."""

import os
import stat
import time

import pytest

from scion_sdk.exchange.staging import (
    ExchangeRunError,
    RunStaging,
    run_id_from_meta,
    sweep_expired,
)
from tests.exchange.exchange_support import configure


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.2")
def test_run_dirs_are_private_disjoint_and_swept(tmp_path, monkeypatch):
    settings = configure(tmp_path, monkeypatch)
    staging = RunStaging.open(settings, "app", run_id_from_meta({"scion/run": "run-1"}))
    for directory in (staging.input_dir, staging.output_dir):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert not staging.input_dir.is_relative_to(staging.output_dir.parent.parent)
    (staging.output_dir / "out.png").write_bytes(b"x")
    os.symlink(tmp_path / "exchange.json", staging.output_dir / "link.json")
    assert [p.name for p in staging.outputs()] == ["out.png"]

    with pytest.raises(ExchangeRunError) as excinfo:
        run_id_from_meta({"scion/run": "../escape"})
    assert excinfo.value.code == "EXCHANGE_RUN_ID_INVALID"
    assert len(run_id_from_meta(None)) == 25

    old = RunStaging.open(settings, "app", "old-run")
    past = time.time() - settings.retention_s - 10
    os.utime(old.output_dir, (past, past))
    assert sweep_expired(settings.staging_root, settings.retention_s, force=True) >= 1
    assert not old.output_dir.exists() and staging.output_dir.exists()
