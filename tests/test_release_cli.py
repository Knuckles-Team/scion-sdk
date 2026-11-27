"""``connector-release-digest`` prints the digests a release record carries (R650.1.2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from test_release_digest import FILES, _wheel

from scion_sdk.certify import release_cli
from scion_sdk.manifest.release_digest import CONTENT_PREFIX


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.2")
def test_digest_command_prints_pins_and_refuses_a_bad_wheel(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wheel = _wheel(tmp_path / "digestdemo-1.0.0-py3-none-any.whl", FILES)
    sdist = tmp_path / "digestdemo-1.0.0.tar.gz"
    sdist.write_bytes(b"source")
    assert release_cli.main([str(wheel), "--sdist", str(sdist)]) == 0
    printed = json.loads(capsys.readouterr().out)
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert printed["distribution"] == "digestdemo" and printed["version"] == "1.0.0"
    assert printed["wheel_sha256"] == wheel_sha
    assert printed["sdist_sha256"] == hashlib.sha256(b"source").hexdigest()
    assert printed["artifact_digests"] == sorted(
        [
            f"sha256:{wheel_sha}",
            f"sha256:{printed['sdist_sha256']}",
            f"{CONTENT_PREFIX}{printed['content_sha256']}",
        ]
    )
    assert release_cli.main([str(tmp_path / "absent.whl")]) == 2
    assert capsys.readouterr().out == ""
