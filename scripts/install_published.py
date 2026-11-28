#!/usr/bin/env python3
"""Install the just-published distribution, waiting for the package index to serve it.

SCION-CENTRALIZED-CAPABILITIES-R046.3: the post-publish import check installs
``scion-sdk==<tag version>`` seconds after ``uv publish`` returns, and the
index can take longer than that to list the new version (the 0.13.0, 0.13.1
and 0.13.2 checks failed with "no version of scion-sdk" within seconds of the
upload). The install therefore retries with exponential backoff for a bounded
time and bypasses uv's cached index answer, so a version that never appears
still fails the job, with the last installer output.

Usage: ``python3 scripts/install_published.py PYTHON REQUIREMENT [--deadline S]``
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

DEADLINE_SECONDS = 600.0
FIRST_DELAY_SECONDS = 5.0
DELAY_CAP_SECONDS = 60.0


def install_published(
    python: str,
    requirement: str,
    *,
    deadline_seconds: float = DEADLINE_SECONDS,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the install until it succeeds or ``deadline_seconds`` have passed.

    Returns the installer's last exit code: ``0`` on success.
    """
    started = clock()
    delay = FIRST_DELAY_SECONDS
    attempt = 0
    while True:
        attempt += 1
        result = run(
            ["uv", "pip", "install", "--refresh", "--python", python, requirement],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"installed {requirement} on attempt {attempt}")
            return 0
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 0:
            sys.stdout.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
            print(
                f"{requirement} was not installable after {attempt} attempts "
                f"in {deadline_seconds:.0f}s",
                file=sys.stderr,
            )
            return result.returncode
        wait = min(delay, remaining)
        print(f"attempt {attempt} failed; retrying in {wait:.0f}s")
        sleep(wait)
        delay = min(delay * 2, DELAY_CAP_SECONDS)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("python")
    parser.add_argument("requirement")
    parser.add_argument("--deadline", type=float, default=DEADLINE_SECONDS)
    args = parser.parse_args(argv)
    return install_published(
        args.python, args.requirement, deadline_seconds=args.deadline
    )


if __name__ == "__main__":
    sys.exit(main())
