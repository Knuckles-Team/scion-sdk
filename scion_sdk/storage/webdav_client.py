"""WebDAV path operations over the governed client (SCION-CENTRALIZED-CAPABILITIES-R042.5).

:class:`DavClient` speaks WebDAV (RFC 4918) to one collection root. Every
request goes through :func:`scion_sdk.http.client.create_http_client` with
address pinning, so the egress guard and the TLS profile apply. Multistatus
bodies are bounded and parsed with a parser that refuses document type
declarations and entities. :class:`scion_sdk.storage.webdav.WebDavBackend`
builds the bucket and object protocol on top of these path methods.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit
from xml.etree.ElementTree import (
    Element,  # nosec B405 - type only; parsing uses defusedxml
)

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from scion_sdk.http.client import create_http_client
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.storage.base import (
    AlreadyExistsError,
    InvalidNameError,
    NotFoundError,
    StorageError,
    TooLargeError,
    validate_key,
)

if TYPE_CHECKING:
    from scion_sdk.tls.profile import ResolvedTLSProfile

__all__ = ["DEFAULT_MAX_XML_BYTES", "DavClient", "DavEntry"]

#: Largest multistatus body the client parses (R042.5 knob ``max_xml_bytes``).
DEFAULT_MAX_XML_BYTES = 4 * 1024 * 1024
_DAV = "DAV:"
_CHUNK = 64 * 1024
_STANDARD_PROPS = (
    "getlastmodified",
    "getcontentlength",
    "getcontenttype",
    "resourcetype",
    "getetag",
)


@dataclass(frozen=True)
class DavEntry:
    """One resource of a multistatus listing, relative to the client root."""

    path: str
    name: str
    is_collection: bool
    size: int = 0
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    props: dict[str, str] = field(default_factory=dict)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _clean_path(path: str) -> str:
    """A relative resource path: empty for the root, else a validated key."""
    stripped = path.strip("/")
    if not stripped:
        return ""
    if "\\" in path or path.startswith("/"):
        raise InvalidNameError("WebDAV path must be relative")
    return validate_key(stripped)


def _propfind_body(extra: Sequence[tuple[str, str]]) -> bytes:
    namespaces = {
        ns: f"x{index}" for index, ns in enumerate(dict.fromkeys(n for n, _ in extra))
    }
    declarations = "".join(f' xmlns:{p}="{ns}"' for ns, p in namespaces.items())
    props = "".join(f"<d:{name}/>" for name in _STANDARD_PROPS)
    props += "".join(f"<{namespaces[ns]}:{name}/>" for ns, name in extra)
    return (
        f'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"'
        f"{declarations}><d:prop>{props}</d:prop></d:propfind>"
    ).encode()


def _text(prop: Element | None, name: str) -> str | None:
    if prop is None:
        return None
    node = prop.find(f"{{{_DAV}}}{name}")
    return node.text if node is not None and node.text else None


class DavClient:
    """WebDAV path operations against one collection root."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        tls: ResolvedTLSProfile | None = None,
        allowed_private_hosts: tuple[str, ...] = (),
        transport: httpx.BaseTransport | None = None,
        max_xml_bytes: int = DEFAULT_MAX_XML_BYTES,
    ) -> None:
        """``transport`` replaces the network for tests; it disables pinning."""
        root = base_url.rstrip("/") + "/"
        host = (urlsplit(root).hostname or "").lower()
        allowed = tuple(allowed_private_hosts)
        auth = httpx.BasicAuth(username, password or "") if username else None
        options = HttpClientOptions(
            base_url=root,
            tls=tls,
            auth=auth,
            allow_plaintext=host in {h.lower() for h in allowed},
            pin_egress=transport is None,
            allowed_private_hosts=allowed if transport is None else (),
            allow_loopback=transport is None and _is_loopback(host),
        )
        self._root_path = unquote(urlsplit(root).path)
        self._http = create_http_client(options, transport=transport)
        self._max_xml_bytes = max_xml_bytes

    # -- plumbing -------------------------------------------------------------
    @staticmethod
    def _url(path: str) -> str:
        clean = _clean_path(path)
        return "/".join(quote(segment, safe="") for segment in clean.split("/"))

    def _absolute(self, path: str) -> str:
        return str(self._http.base_url.join(self._url(path)))

    @staticmethod
    def _check(response: httpx.Response, context: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise NotFoundError(f"{context} not found.")
        if status in (405, 412):
            raise AlreadyExistsError(f"{context} already exists.")
        raise StorageError(f"{context}: WebDAV status {status}")

    def _bounded(
        self, chunks: Iterable[bytes], limit: int, error: type[StorageError]
    ) -> bytes:
        body = bytearray()
        for chunk in chunks:
            body.extend(chunk)
            if len(body) > limit:
                raise error(f"WebDAV body exceeds {limit} bytes")
        return bytes(body)

    def _relative(self, href: str) -> str:
        path = unquote(urlsplit(href).path)
        if path.startswith(self._root_path):
            path = path[len(self._root_path) :]
        return path.strip("/")

    def _entries(
        self, body: bytes, extra: Sequence[tuple[str, str]]
    ) -> Iterator[DavEntry]:
        try:
            root = safe_fromstring(body, forbid_dtd=True)
        except (DefusedXmlException, SyntaxError):
            raise StorageError("WebDAV listing is not a safe XML document") from None
        for response in root.iter(f"{{{_DAV}}}response"):
            href = response.findtext(f"{{{_DAV}}}href") or ""
            prop = response.find(f".//{{{_DAV}}}prop")
            yield self._entry(self._relative(href), prop, extra)

    @staticmethod
    def _entry(
        path: str, prop: Element | None, extra: Sequence[tuple[str, str]]
    ) -> DavEntry:
        kind = prop.find(f"{{{_DAV}}}resourcetype") if prop is not None else None
        is_collection = (
            kind is not None and kind.find(f"{{{_DAV}}}collection") is not None
        )
        props: dict[str, str] = {}
        for ns, name in extra:
            node = prop.find(f"{{{ns}}}{name}") if prop is not None else None
            props[name] = node.text or "" if node is not None else ""
        etag = _text(prop, "getetag")
        return DavEntry(
            path=path,
            name=path.rsplit("/", 1)[-1],
            is_collection=is_collection,
            size=int(_text(prop, "getcontentlength") or 0),
            etag=etag.strip('"') if etag else None,
            last_modified=_text(prop, "getlastmodified"),
            content_type=_text(prop, "getcontenttype"),
            props=props,
        )

    # -- path methods -----------------------------------------------------------
    def propfind(
        self,
        path: str = "",
        depth: int = 1,
        extra_props: Sequence[tuple[str, str]] = (),
    ) -> list[DavEntry]:
        """List ``path`` (depth 1) or describe it (depth 0)."""
        headers = {"Depth": str(depth), "Content-Type": "application/xml"}
        url = self._url(path)
        with self._http.stream(
            "PROPFIND", url, content=_propfind_body(extra_props), headers=headers
        ) as response:
            self._check(response, "WebDAV resource")
            body = self._bounded(
                response.iter_bytes(_CHUNK), self._max_xml_bytes, StorageError
            )
        return list(self._entries(body, extra_props))

    def read_path(self, path: str, max_bytes: int | None = None) -> bytes:
        """Download one file; stop at ``max_bytes`` with ``STORAGE_TOO_LARGE``."""
        with self._http.stream("GET", self._url(path)) as response:
            self._check(response, "WebDAV file")
            limit = max_bytes if max_bytes is not None else 1 << 62
            return self._bounded(response.iter_bytes(_CHUNK), limit, TooLargeError)

    def make_collection(self, path: str, *, exist_ok: bool = True) -> None:
        """Create one collection; an existing one passes when ``exist_ok``."""
        response = self._http.request("MKCOL", self._url(path))
        if response.status_code == 405 and exist_ok:
            return
        if response.status_code == 409:
            raise NotFoundError("WebDAV parent collection not found.")
        self._check(response, "WebDAV collection")

    def write_path(
        self,
        path: str,
        data: bytes,
        content_type: str | None = None,
        *,
        overwrite: bool = True,
        make_parents: bool = True,
    ) -> None:
        """Upload one file, creating missing parent collections first."""
        clean = _clean_path(path)
        if make_parents:
            parts = clean.split("/")[:-1]
            for index in range(1, len(parts) + 1):
                self.make_collection("/".join(parts[:index]))
        headers = {"Content-Type": content_type} if content_type else {}
        if not overwrite:
            headers["If-None-Match"] = "*"
        response = self._http.put(self._url(clean), content=data, headers=headers)
        self._check(response, "WebDAV file")

    def delete_path(self, path: str) -> None:
        """Delete one file or collection."""
        self._check(self._http.delete(self._url(path)), "WebDAV resource")

    def _transfer(self, method: str, source: str, target: str, overwrite: bool) -> None:
        headers = {
            "Destination": self._absolute(target),
            "Overwrite": "T" if overwrite else "F",
        }
        response = self._http.request(method, self._url(source), headers=headers)
        self._check(
            response,
            "WebDAV destination" if response.status_code == 412 else "WebDAV source",
        )

    def copy_path(self, source: str, target: str, *, overwrite: bool = False) -> None:
        """Copy one resource on the server."""
        self._transfer("COPY", source, target, overwrite)

    def move_path(self, source: str, target: str, *, overwrite: bool = False) -> None:
        """Move one resource on the server."""
        self._transfer("MOVE", source, target, overwrite)

    def close(self) -> None:
        """Close the governed client."""
        self._http.close()

    def __enter__(self) -> DavClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
