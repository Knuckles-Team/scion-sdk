"""Per-run staging directories (SCION-CENTRALIZED-CAPABILITIES-R043.2).

Each run gets a private input directory and a private output directory under
two disjoint trees, ``<root>/in`` and ``<root>/out``. A pooled child process
receives the two trees as its read and write roots once, at spawn, and each
call then works inside its own run directories.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scion_sdk.exchange.config import ExchangeSettings

__all__ = [
    "EXCHANGE_RUN_ID_INVALID",
    "RUN_META_KEY",
    "ExchangeRunError",
    "RunStaging",
    "input_tree",
    "output_tree",
    "run_id_from_meta",
    "sweep_expired",
]

EXCHANGE_RUN_ID_INVALID = "EXCHANGE_RUN_ID_INVALID"
#: Request metadata key that groups several calls into one run.
RUN_META_KEY = "scion/run"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SWEEP_INTERVAL_S = 60.0
_sweep_lock = threading.Lock()
_last_sweep: dict[str, float] = {}


class ExchangeRunError(ValueError):
    """A run cannot open; ``code`` names the refusal."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def input_tree(root: Path) -> Path:
    """The tree that holds every run's staged inputs."""
    return root / "in"


def output_tree(root: Path) -> Path:
    """The tree that holds every run's outputs."""
    return root / "out"


def _new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


def run_id_from_meta(meta: Mapping[str, Any] | None) -> str:
    """The caller's run ID from request metadata, or a fresh one.

    Raises:
        ExchangeRunError: ``EXCHANGE_RUN_ID_INVALID`` for a malformed ID.
    """
    value = (meta or {}).get(RUN_META_KEY)
    if value is None:
        return _new_run_id()
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise ExchangeRunError(EXCHANGE_RUN_ID_INVALID, "scion/run is malformed")
    return value


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


@dataclass
class RunStaging:
    """The two private directories of one run."""

    namespace: str
    run_id: str
    input_dir: Path
    output_dir: Path
    started: float = field(default_factory=time.time)

    @classmethod
    def open(
        cls, settings: ExchangeSettings, namespace: str, run_id: str
    ) -> RunStaging:
        """Create (or reopen) the run's directories with mode 0700."""
        root = settings.staging_root
        sweep_expired(root, settings.retention_s)
        for tree in (root, input_tree(root), output_tree(root)):
            _private_dir(tree)
        return cls(
            namespace=namespace,
            run_id=run_id,
            input_dir=_private_dir(input_tree(root) / namespace / run_id),
            output_dir=_private_dir(output_tree(root) / namespace / run_id),
        )

    def _fresh(self, path: Path) -> bool:
        # A reopened run publishes only what this call wrote (1 s clock slack).
        return path.stat().st_mtime >= self.started - 1.0

    def outputs(self) -> list[Path]:
        """Files this call wrote under the output directory; links are skipped."""
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.output_dir, followlinks=False):
            base = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not (base / d).is_symlink())
            for name in sorted(filenames):
                path = base / name
                if path.is_file() and not path.is_symlink() and self._fresh(path):
                    found.append(path)
        return found


def _expired(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def sweep_expired(root: Path, retention_s: int, *, force: bool = False) -> int:
    """Delete run directories older than ``retention_s``; at most once per 60 s."""
    key = str(root)
    now = time.time()
    with _sweep_lock:
        if not force and now - _last_sweep.get(key, 0.0) < _SWEEP_INTERVAL_S:
            return 0
        _last_sweep[key] = now
    cutoff, removed = now - retention_s, 0
    for tree in (input_tree(root), output_tree(root)):
        for run_dir in tree.glob("*/*"):
            if (
                run_dir.is_dir()
                and not run_dir.is_symlink()
                and _expired(run_dir, cutoff)
            ):
                shutil.rmtree(run_dir, ignore_errors=True)
                removed += 1
    return removed
