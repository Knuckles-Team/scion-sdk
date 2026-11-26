"""Mount value types and path/file helpers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import posixpath
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MountEntry(_Model):
    path: str
    mode: str
    digest: str


class PendingEntry(_Model):
    path: str
    operation: Literal["put", "delete"]
    blob_ref: str | None = None


class MountState(_Model):
    repository_id: str
    base_revision: str
    prefixes: tuple[str, ...] = Field(min_length=1)
    pending_changeset_id: str | None = None


class MountRefusedError(RuntimeError):
    """The mount refused to start, read or write; ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _clean(path: str) -> str:
    pure = PurePosixPath(path)
    if (
        not path
        or pure.is_absolute()
        or "\\" in path
        or any(part in ("", ".", "..") for part in path.split("/"))
        or posixpath.normpath(path) != path
    ):
        raise MountRefusedError("path_denied")
    return path


def _require_declared(prefixes: tuple[str, ...], path: str) -> None:
    if not any(path == p or path.startswith(p + "/") for p in prefixes):
        raise MountRefusedError("path_denied")


def _checked_path(prefixes: tuple[str, ...], path: str) -> str:
    path = _clean(path)
    _require_declared(prefixes, path)
    return path


def _materialize(root: Path, path: str, data: bytes) -> None:
    target = root.joinpath(*path.split("/"))
    walker = root
    for part in path.split("/"):
        walker = walker / part
        if walker.is_symlink():
            raise MountRefusedError("path_denied")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(root):
            raise MountRefusedError("path_denied")
    except OSError:
        raise MountRefusedError("path_denied") from None
    target.write_bytes(data)


def _changeset_digest(pending: tuple[PendingEntry, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [item.model_dump() for item in pending],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


MountBackendName = Literal["overlay", "fuse"]
_REQUESTS = ("auto", "overlay", "fuse")


def _device_usable() -> bool:
    return os.access("/dev/fuse", os.R_OK | os.W_OK)


def _fuse_importable() -> bool:
    return importlib.util.find_spec("pyfuse3") is not None


def select_backend(
    requested: str,
    *,
    device_usable: Callable[[], bool] = _device_usable,
    fuse_importable: Callable[[], bool] = _fuse_importable,
) -> MountBackendName:
    """Resolve ``auto``, ``overlay`` or ``fuse`` to the backend that will run.

    ``auto`` picks ``fuse`` only when ``/dev/fuse`` is readable and writable and
    ``pyfuse3`` imports; otherwise ``overlay``. An explicit ``fuse`` without both
    refuses with ``fuse_unavailable``.
    """
    if requested not in _REQUESTS:
        raise MountRefusedError("unknown_backend")
    can_fuse = device_usable() and fuse_importable()
    if requested == "fuse":
        if not can_fuse:
            raise MountRefusedError("fuse_unavailable")
        return "fuse"
    if requested == "auto" and can_fuse:
        return "fuse"
    return "overlay"
