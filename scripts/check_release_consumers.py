#!/usr/bin/env python3
"""Install a candidate scion-sdk wheel into pinned consumers before upload.

SCION-QUALITY-RELEASE-R007: removing or moving a public path is a hard cutover,
so the consumers must already import cleanly against the candidate. For every
consumer in ``release_consumers.toml`` this script clones the consumer at its
declared ref, installs it into a fresh virtual environment with the candidate
wheel overriding the consumer's own scion-sdk requirement, imports its package
and collects its tests. Any non-zero step fails the release before the wheel is
published.

Usage: ``python3 scripts/check_release_consumers.py --wheel dist/scion_sdk-X.whl``
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "release_consumers.toml"

Runner = Callable[[Sequence[str], Path | None], int]


@dataclass(frozen=True)
class Consumer:
    repo: str
    ref: str
    import_module: str
    extras: tuple[str, ...]
    tests: str

    @property
    def name(self) -> str:
        return self.repo.rsplit("/", 1)[-1]


def load_consumers(path: Path = CONFIG) -> list[Consumer]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = data.get("consumer", [])
    if not rows:
        raise ValueError("release_consumers.toml declares no consumer")
    return [
        Consumer(
            repo=row["repo"],
            ref=row.get("ref", "main"),
            import_module=row["import_module"],
            extras=tuple(row.get("extras", ())),
            tests=row.get("tests", "tests"),
        )
        for row in rows
    ]


def plan(
    consumer: Consumer, wheel: Path, workdir: Path
) -> list[tuple[list[str], Path | None]]:
    """The ordered commands that check one consumer, each with its cwd."""
    src = workdir / consumer.name
    venv = workdir / f"{consumer.name}.venv"
    py = str(venv / "bin" / "python")
    override = workdir / f"{consumer.name}.override.txt"
    target = f"{src}[{','.join(consumer.extras)}]" if consumer.extras else str(src)
    return [
        (
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                consumer.ref,
                f"https://github.com/{consumer.repo}.git",
                str(src),
            ],
            None,
        ),
        (["uv", "venv", str(venv)], None),
        (
            [
                "uv",
                "pip",
                "install",
                "--python",
                py,
                "--override",
                str(override),
                target,
                "pytest",
            ],
            None,
        ),
        ([py, "-c", f"import {consumer.import_module}"], src),
        ([py, "-m", "pytest", "--collect-only", "-q", consumer.tests], src),
    ]


def check_consumer(
    consumer: Consumer, wheel: Path, workdir: Path, run: Runner
) -> str | None:
    """None when the consumer passes, else the failing command line."""
    (workdir / f"{consumer.name}.override.txt").write_text(
        f"scion-sdk @ {wheel.resolve().as_uri()}\n", encoding="utf-8"
    )
    for command, cwd in plan(consumer, wheel, workdir):
        if run(command, cwd) != 0:
            return " ".join(command)
    return None


def check_all(
    consumers: Sequence[Consumer], wheel: Path, workdir: Path, run: Runner
) -> dict[str, str]:
    """Failing command per consumer name; empty when every consumer passes."""
    failures: dict[str, str] = {}
    for consumer in consumers:
        failed = check_consumer(consumer, wheel, workdir, run)
        if failed is not None:
            failures[consumer.name] = failed
    return failures


def _run(command: Sequence[str], cwd: Path | None) -> int:
    return subprocess.run(list(command), cwd=cwd, check=False).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.workdir.mkdir(parents=True, exist_ok=True)
    failures = check_all(load_consumers(args.config), args.wheel, args.workdir, _run)
    for name, command in failures.items():
        print(f"consumer {name} failed: {command}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
