"""FUSE backend of the virtual workspace mount (SCION-SCM-R002.3).

``FuseBackend`` serves ``listdir``, ``read``, ``write`` and ``delete`` over the
same :class:`~scion_sdk.source.mount.SourceMount` and its source client, so
writes stay in the staged changeset. No clone, object database or full
checkout is kept. ``pyfuse3`` is imported only when a mount is served.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from scion_sdk.source._mount_models import MountEntry, MountRefusedError

if TYPE_CHECKING:
    from scion_sdk.source.mount import SourceMount

__all__ = ["FuseBackend"]


class FuseBackend:
    """The four path operations a FUSE adapter needs, over one mount."""

    def __init__(self, mount: SourceMount) -> None:
        self._mount = mount

    async def listdir(self, directory: str) -> tuple[MountEntry, ...]:
        return await self._mount.listdir(directory)

    async def read(self, path: str) -> bytes:
        return await self._mount.read(path)

    def write(self, path: str, data: bytes) -> None:
        self._mount.write(path, data)

    def delete(self, path: str) -> None:
        self._mount.delete(path)

    def operations(self) -> Any:
        """Return the ``pyfuse3`` operations object over this backend.

        The caller runs it under ``pyfuse3.init`` and ``pyfuse3.main``; a host
        without ``pyfuse3`` refuses with ``fuse_unavailable``.
        """
        try:
            pyfuse3 = importlib.import_module("pyfuse3")
        except ImportError:
            raise MountRefusedError("fuse_unavailable") from None
        return _operations(pyfuse3, self)


def _operations(pyfuse3: Any, backend: FuseBackend) -> Any:
    """Path-keyed ``pyfuse3.Operations``: inode numbers map to mount paths."""
    import errno
    import stat

    paths: dict[int, str] = {pyfuse3.ROOT_INODE: ""}
    inodes: dict[str, int] = {"": pyfuse3.ROOT_INODE}

    def inode_of(path: str) -> int:
        if path not in inodes:
            number = len(paths) + pyfuse3.ROOT_INODE
            inodes[path], paths[number] = number, path
        return inodes[path]

    def refuse(code: int) -> Any:
        return pyfuse3.FUSEError(code)

    class _Operations(pyfuse3.Operations):  # type: ignore[misc]
        async def lookup(self, parent: int, name: bytes, ctx: Any) -> Any:
            path = "/".join(p for p in (paths[parent], name.decode()) if p)
            try:
                return await self.getattr(inode_of(path), ctx)
            except MountRefusedError:
                raise refuse(errno.ENOENT) from None

        async def getattr(self, inode: int, ctx: Any = None) -> Any:
            attrs = pyfuse3.EntryAttributes()
            attrs.st_ino = inode
            path = paths[inode]
            if not path:
                attrs.st_mode = stat.S_IFDIR | 0o755
                return attrs
            if await backend.listdir(path):
                attrs.st_mode = stat.S_IFDIR | 0o755
                return attrs
            attrs.st_mode = stat.S_IFREG | 0o644
            attrs.st_size = len(await backend.read(path))
            return attrs

        async def readdir(self, fh: int, start: int, token: Any) -> None:
            for number, entry in enumerate(await backend.listdir(paths[fh])):
                if number < start:
                    continue
                name = entry.path.rsplit("/", 1)[-1].encode()
                attrs = await self.getattr(inode_of(entry.path))
                if not pyfuse3.readdir_reply(token, name, attrs, number + 1):
                    return

        async def opendir(self, inode: int, ctx: Any) -> int:
            return inode

        async def open(self, inode: int, flags: int, ctx: Any) -> Any:
            return pyfuse3.FileInfo(fh=inode)

        async def read(self, fh: int, offset: int, size: int) -> bytes:
            return (await backend.read(paths[fh]))[offset : offset + size]

        async def write(self, fh: int, offset: int, buf: bytes) -> int:
            try:
                current = await backend.read(paths[fh])
            except MountRefusedError:
                current = b""
            backend.write(
                paths[fh],
                current[:offset].ljust(offset, b"\0")
                + buf
                + current[offset + len(buf) :],
            )
            return len(buf)

        async def create(
            self, parent: int, name: bytes, mode: int, flags: int, ctx: Any
        ) -> Any:
            path = "/".join(p for p in (paths[parent], name.decode()) if p)
            backend.write(path, b"")
            inode = inode_of(path)
            return pyfuse3.FileInfo(fh=inode), await self.getattr(inode)

        async def unlink(self, parent: int, name: bytes, ctx: Any) -> None:
            backend.delete("/".join(p for p in (paths[parent], name.decode()) if p))

    return _Operations()
