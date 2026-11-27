"""The wheel content digest is equal for the wheel and its install, and covers every file (R650.2)."""

from __future__ import annotations

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

from scion_sdk.manifest.release_digest import (
    ReleaseDigestError,
    installed_release_evidence,
    wheel_release_evidence,
)

DIST = "digestdemo"
FILES = {
    "digestdemo/__init__.py": b"VALUE = 1\n",
    "digestdemo/ontology/demo.ttl": b"<urn:a> a <urn:B> .\n",
}


def _record_row(path: str, body: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=")
    return f"{path},sha256={digest.decode()},{len(body)}"


def _members(files: dict[str, bytes]) -> dict[str, bytes]:
    info = "digestdemo-1.0.0.dist-info"
    members = dict(files)
    members[f"{info}/METADATA"] = (
        b"Metadata-Version: 2.1\nName: digestdemo\nVersion: 1.0.0\n"
    )
    members[f"{info}/WHEEL"] = b"Wheel-Version: 1.0\n"
    rows = [_record_row(path, body) for path, body in members.items()]
    rows.append(f"{info}/RECORD,,")
    members[f"{info}/RECORD"] = ("\n".join(rows) + "\n").encode()
    return members


def _wheel(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in _members(files).items():
            archive.writestr(name, body)
    return path


def _install(site: Path, files: dict[str, bytes]) -> None:
    members = _members(files)
    for name, body in members.items():
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    info = site / "digestdemo-1.0.0.dist-info"
    (info / "INSTALLER").write_text("pip\n")  # an installer file: not in the digest
    (info / "RECORD").write_text(
        (info / "RECORD").read_text() + "digestdemo-1.0.0.dist-info/INSTALLER,,\n"
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.2")
def test_wheel_and_install_agree_and_every_file_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = wheel_release_evidence(_wheel(tmp_path / "a.whl", FILES))
    site = tmp_path / "site"
    _install(site, FILES)
    monkeypatch.syspath_prepend(str(site))
    sys.modules.pop("digestdemo", None)
    installed = installed_release_evidence(DIST)
    assert (
        installed == built and built.distribution == DIST and built.version == "1.0.0"
    )
    changed = dict(FILES, **{"digestdemo/ontology/demo.ttl": b"<urn:a> a <urn:C> .\n"})
    other = wheel_release_evidence(_wheel(tmp_path / "b.whl", changed))
    assert other.content_sha256 != built.content_sha256
    extra = wheel_release_evidence(
        _wheel(tmp_path / "c.whl", dict(FILES, **{"digestdemo/x.py": b""}))
    )
    assert extra.content_sha256 != built.content_sha256


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.2")
def test_an_edit_after_install_or_a_missing_install_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    _install(site, FILES)
    monkeypatch.syspath_prepend(str(site))
    (site / "digestdemo/ontology/demo.ttl").write_bytes(b"tampered\n")
    with pytest.raises(ReleaseDigestError):
        installed_release_evidence(DIST)
    with pytest.raises(ReleaseDigestError):
        installed_release_evidence("not-an-installed-distribution")
    with pytest.raises(ReleaseDigestError):
        wheel_release_evidence(tmp_path / "missing.whl")
