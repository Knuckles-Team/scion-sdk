"""An in-memory WebDAV server double for the storage and exchange tests.

It serves PROPFIND (depth 0 and 1), GET, PUT, MKCOL, DELETE, COPY and MOVE
over a path map, through ``httpx.MockTransport``. It records every request
method and path so a test can assert the order of operations.
"""

from __future__ import annotations

from email.utils import formatdate
from urllib.parse import quote, unquote, urlsplit

import httpx

ROOT = "/dav/files/svc"
BASE_URL = f"https://files.example{ROOT}"


class DavDouble:
    """A WebDAV collection tree held in memory."""

    def __init__(self, *, doctype: bool = False) -> None:
        self.files: dict[str, bytes] = {}
        self.collections: set[str] = {""}
        self.requests: list[tuple[str, str]] = []
        self.doctype = doctype

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _path(self, url: str) -> str:
        path = unquote(urlsplit(url).path)
        return (
            path[len(ROOT) :].strip("/") if path.startswith(ROOT) else path.strip("/")
        )

    def _parent_exists(self, path: str) -> bool:
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        return parent in self.collections

    def _entry(self, path: str) -> str:
        href = quote(
            f"{ROOT}/{path}".rstrip("/") + ("/" if path in self.collections else "")
        )
        if path in self.collections:
            props = "<d:resourcetype><d:collection/></d:resourcetype>"
        else:
            size = len(self.files[path])
            props = (
                f"<d:resourcetype/><d:getcontentlength>{size}</d:getcontentlength>"
                f'<d:getetag>"e{size}"</d:getetag><d:getcontenttype>application/octet-stream</d:getcontenttype>'
            )
        props += f"<d:getlastmodified>{formatdate(usegmt=True)}</d:getlastmodified>"
        extra = '<oc:fileid xmlns:oc="http://owncloud.org/ns">42</oc:fileid>'
        return f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}{extra}</d:prop></d:propstat></d:response>"

    def _propfind(self, path: str, depth: str) -> httpx.Response:
        if path not in self.collections and path not in self.files:
            return httpx.Response(404)
        paths = [path]
        if depth == "1" and path in self.collections:
            prefix = f"{path}/" if path else ""
            children = {
                p
                for p in (*self.files, *self.collections)
                if p.startswith(prefix) and p != path
            }
            paths += sorted(p for p in children if "/" not in p[len(prefix) :])
        doctype = '<!DOCTYPE x [<!ENTITY a "b">]>' if self.doctype else ""
        body = f'<?xml version="1.0"?>{doctype}<d:multistatus xmlns:d="DAV:">{"".join(self._entry(p) for p in paths)}</d:multistatus>'
        return httpx.Response(207, content=body.encode())

    def _transfer(
        self, request: httpx.Request, path: str, move: bool
    ) -> httpx.Response:
        target = self._path(request.headers["Destination"])
        if path not in self.files:
            return httpx.Response(404)
        if target in self.files and request.headers.get("Overwrite") == "F":
            return httpx.Response(412)
        if not self._parent_exists(target):
            return httpx.Response(409)
        self.files[target] = self.files[path]
        if move:
            del self.files[path]
        return httpx.Response(201)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = self._path(str(request.url))
        method = request.method
        self.requests.append((method, path))
        if method == "PROPFIND":
            return self._propfind(path, request.headers.get("Depth", "1"))
        if method == "GET":
            data = self.files.get(path)
            return (
                httpx.Response(200, content=data)
                if data is not None
                else httpx.Response(404)
            )
        if method == "PUT":
            if not self._parent_exists(path):
                return httpx.Response(409)
            if request.headers.get("If-None-Match") == "*" and path in self.files:
                return httpx.Response(412)
            self.files[path] = request.read()
            return httpx.Response(201)
        if method == "MKCOL":
            if path in self.collections or path in self.files:
                return httpx.Response(405)
            if not self._parent_exists(path):
                return httpx.Response(409)
            self.collections.add(path)
            return httpx.Response(201)
        if method == "DELETE":
            return self._delete(path)
        if method in ("COPY", "MOVE"):
            return self._transfer(request, path, method == "MOVE")
        return httpx.Response(405)

    def _delete(self, path: str) -> httpx.Response:
        if path in self.files:
            del self.files[path]
            return httpx.Response(204)
        if path in self.collections:
            prefix = f"{path}/"
            self.files = {
                k: v for k, v in self.files.items() if not k.startswith(prefix)
            }
            self.collections = {
                c for c in self.collections if c != path and not c.startswith(prefix)
            }
            return httpx.Response(204)
        return httpx.Response(404)
