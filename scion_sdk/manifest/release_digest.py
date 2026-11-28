"""The content digest of a built connector wheel (SCION-CONNECTOR-CONTROL-R650.2).

The digest covers every file the wheel installs into the environment: the
code, the generated ontology and declaration artifacts and the metadata. It is
computed from the wheel's ``RECORD`` at release time and from the installed
distribution's ``RECORD`` at run time, and the two are equal for one wheel. At
run time each file is also hashed from disk, so an edit made after install
changes the answer. Files that an installer adds (``RECORD``, ``INSTALLER``,
``REQUESTED``, ``direct_url.json``, ``uv_cache.json``), bytecode and files outside the package
directories (console scripts) do not take part.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from email import message_from_bytes
from importlib import metadata
from pathlib import Path

__all__ = [
    "CONTENT_PREFIX",
    "ReleaseDigestError",
    "ReleaseEvidence",
    "installed_release_evidence",
    "read_release_evidence",
    "wheel_release_evidence",
]

#: Prefix of a content digest inside a release record's artifact digests.
CONTENT_PREFIX = "content-sha256:"
_DOMAIN = b"scion-wheel-content-v1\0"
_INSTALLER_FILES = frozenset(
    {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json"}
)
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class ReleaseDigestError(ValueError):
    """A wheel or an installed distribution does not yield one trustworthy digest."""


@dataclass(frozen=True)
class ReleaseEvidence:
    """What a connector reports about the wheel it runs from."""

    distribution: str
    version: str
    content_sha256: str

    @property
    def pin(self) -> str:
        """The string a release record carries for this content digest."""
        return f"{CONTENT_PREFIX}{self.content_sha256}"


def read_release_evidence(text: str) -> ReleaseEvidence:
    """Parse the ``connector://release`` body; unknown or missing keys refuse."""
    try:
        document = json.loads(text)
    except ValueError:
        raise ReleaseDigestError("release evidence is not JSON") from None
    keys = {"distribution", "version", "content_sha256"}
    if not isinstance(document, dict) or set(document) != keys:
        raise ReleaseDigestError("release evidence has unexpected keys")
    values = [document[name] for name in sorted(keys)]
    if not all(isinstance(value, str) and value for value in values):
        raise ReleaseDigestError("release evidence values must be strings")
    if not _HEX.fullmatch(document["content_sha256"]):
        raise ReleaseDigestError("release evidence digest is malformed")
    return ReleaseEvidence(
        document["distribution"], document["version"], document["content_sha256"]
    )


def _included(path: str) -> bool:
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or "__pycache__" in parts:
        return False
    if path.endswith((".pyc", ".pyo")) or parts[0].endswith(".data"):
        return False
    return not (
        len(parts) == 2
        and parts[0].endswith(".dist-info")
        and parts[1] in _INSTALLER_FILES
    )


def _record_hex(recorded: str) -> str:
    algorithm, _, value = recorded.partition("=")
    if algorithm != "sha256" or not value:
        raise ReleaseDigestError("RECORD names a file without a sha256 hash")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        raise ReleaseDigestError("RECORD holds a malformed hash") from None
    if len(raw) != 32:
        raise ReleaseDigestError("RECORD holds a malformed hash")
    return raw.hex()


def _combine(entries: dict[str, str]) -> str:
    if not entries:
        raise ReleaseDigestError("the distribution lists no files")
    body = "\n".join(f"{path}\0{entries[path]}" for path in sorted(entries))
    return hashlib.sha256(_DOMAIN + body.encode("utf-8")).hexdigest()


def _entries(
    record: str, read: Callable[[str], bytes | None]
) -> Iterable[tuple[str, str]]:
    for row in csv.reader(io.StringIO(record)):
        if not row or not _included(row[0]):
            continue
        recorded = _record_hex(row[1] if len(row) > 1 else "")
        content = read(row[0])
        if content is None or hashlib.sha256(content).hexdigest() != recorded:
            raise ReleaseDigestError("a file differs from its RECORD hash")
        yield row[0], recorded


def _dist_info(names: Iterable[str]) -> str:
    roots = {n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")}
    if len(roots) != 1:
        raise ReleaseDigestError("the wheel must hold exactly one dist-info")
    return roots.pop()


def wheel_release_evidence(wheel: Path) -> ReleaseEvidence:
    """The evidence of a built wheel file, as the release job records it."""
    try:
        with zipfile.ZipFile(wheel) as archive:
            root = _dist_info(archive.namelist())
            info = message_from_bytes(archive.read(f"{root}/METADATA"))
            record = archive.read(f"{root}/RECORD").decode("utf-8")
            entries = dict(_entries(record, lambda p: _read_member(archive, p)))
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise ReleaseDigestError("the wheel is unreadable") from exc
    return ReleaseEvidence(info["Name"], info["Version"], _combine(entries))


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return archive.read(name)
    except KeyError:
        return None


def installed_release_evidence(distribution: str) -> ReleaseEvidence:
    """The evidence of an installed distribution, hashed from the files on disk."""
    try:
        dist = metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        raise ReleaseDigestError("the distribution is not installed") from None
    record = dist.read_text("RECORD")
    if record is None:
        raise ReleaseDigestError("the distribution has no RECORD")

    def read(name: str) -> bytes | None:
        located = Path(str(dist.locate_file(name)))
        return located.read_bytes() if located.is_file() else None

    entries = dict(_entries(record, read))
    return ReleaseEvidence(dist.metadata["Name"], dist.version, _combine(entries))
