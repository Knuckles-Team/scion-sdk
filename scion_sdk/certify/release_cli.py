"""The ``connector-release-digest`` console script.

``connector-release-digest WHEEL [--sdist FILE]`` prints one JSON document for a
built connector wheel: the distribution, the version, the sha256 of the wheel
file, the sha256 of the sdist when given, the content digest the runner
verifies (SCION-CONNECTOR-CONTROL-R650.1.2) and ``artifact_digests``, the strings
a release record carries. No key and no secret take part.

Exit ``0``: printed. ``2``: refused, with the reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from scion_sdk.manifest.release_digest import ReleaseDigestError, wheel_release_evidence

__all__ = ["main"]

_logger = logging.getLogger(__name__)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``connector-release-digest``; returns the process exit code."""
    logging.basicConfig(stream=sys.stderr, format="%(message)s", level=logging.INFO)
    parser = argparse.ArgumentParser(prog="connector-release-digest")
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        evidence = wheel_release_evidence(args.wheel)
        wheel_sha256 = _file_digest(args.wheel)
        sdist_sha256 = _file_digest(args.sdist) if args.sdist else None
    except (ReleaseDigestError, OSError) as error:
        _logger.error("connector-release-digest: %s", error)
        return 2
    digests = [f"sha256:{wheel_sha256}", evidence.pin]
    if sdist_sha256 is not None:
        digests.append(f"sha256:{sdist_sha256}")
    print(
        json.dumps(
            {
                "distribution": evidence.distribution,
                "version": evidence.version,
                "wheel_sha256": wheel_sha256,
                "sdist_sha256": sdist_sha256,
                "content_sha256": evidence.content_sha256,
                "artifact_digests": sorted(digests),
            },
            sort_keys=True,
        )
    )
    return 0
