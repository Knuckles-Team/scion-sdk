"""Shared helpers for the artifact exchange tests (SCION-CENTRALIZED-CAPABILITIES-R043)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scion_sdk.exchange.config import ExchangeSettings, read_exchange_settings
from scion_sdk.exchange.sinks import SinkBackends
from scion_sdk.storage.webdav import WebDavBackend
from tests.storage.dav_double import BASE_URL, DavDouble

EXCHANGE_ENV = (
    "SCION_EXCHANGE_FILE",
    "SCION_EXCHANGE_STAGING_ROOT",
    "SCION_EXCHANGE_MAX_INPUT_BYTES",
    "SCION_EXCHANGE_MAX_OUTPUT_BYTES",
    "SCION_EXCHANGE_MAX_OUTPUTS",
    "SCION_EXCHANGE_RETENTION_S",
    "SCION_EXCHANGE_URL_INPUTS",
    "SCION_EXCHANGE_LINEAGE",
)


def exchange_document(tmp_path: Path, **sink_overrides: object) -> dict:
    """A settings document with a filesystem sink and a WebDAV sink."""
    return {
        "stores": {
            "disk": {"backend": "filesystem", "root": str(tmp_path / "disk")},
            "share": {
                "backend": "webdav",
                "endpoint": BASE_URL,
                "credentials": {
                    "username": "env://EXCHANGE_DAV_USER",
                    "password": "env://EXCHANGE_DAV_PASS",
                },
            },
        },
        "sinks": [
            {
                "name": "objects",
                "store": "disk",
                "bucket": "artifacts",
                "required": True,
                **sink_overrides,
            },
            {"name": "files", "store": "share", "bucket": "ArtCraft"},
        ],
    }


def configure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: dict | None = None,
    **env: str,
) -> ExchangeSettings:
    """Write the document, point the settings at it and load them."""
    for name in EXCHANGE_ENV:
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "exchange.json"
    path.write_text(
        json.dumps(document or exchange_document(tmp_path)), encoding="utf-8"
    )
    monkeypatch.setenv("SCION_EXCHANGE_FILE", str(path))
    monkeypatch.setenv("SCION_EXCHANGE_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setenv("EXCHANGE_DAV_USER", "svc")
    monkeypatch.setenv("EXCHANGE_DAV_PASS", "dav-secret-value")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = read_exchange_settings()
    assert settings is not None
    return settings


def backends_with_dav(settings: ExchangeSettings, dav: DavDouble) -> SinkBackends:
    """Sink backends whose WebDAV store talks to the in-memory double."""
    backends = SinkBackends(settings)
    backends.put(
        "share",
        WebDavBackend(BASE_URL, username="svc", password="pw", transport=dav.transport),
    )
    return backends


SERVED_INIT = """\
from democonn.client import Api, get_client

from scion_sdk.connector import Connector
from scion_sdk.mcp.upstreams import ExchangeBinding, SandboxRoots, UpstreamDecl

UPSTREAMS = (
    UpstreamDecl(
        name="alpha",
        command={command!r},
        args=[{script!r}, "--fixture-kind", "files"],
        sandbox=SandboxRoots(
            read=({data!r},),
            write=({out!r},),
            read_flag="--fixture-read-root",
            write_flag="--fixture-write-root",
        ),
        exchange={exchange},
    ),
)

connector = Connector(
    api=Api,
    get_client=get_client,
    instructions="Demo connector.",
    upstreams=UPSTREAMS,
)
"""


def build_served_connector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, bound: bool = True
) -> None:
    """Build package ``democonn`` with one fixture upstream, bound or not."""
    import sys

    from tests.connector_dist import build
    from tests.upstreams.upstream_fixtures import FIXTURE_SCRIPT, clean_upstream_env

    clean_upstream_env(monkeypatch)
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    init = SERVED_INIT.format(
        command=sys.executable,
        script=str(FIXTURE_SCRIPT),
        data=str(data),
        out=str(out),
        exchange="ExchangeBinding()" if bound else "None",
    )
    pkg_root = tmp_path / "pkg"
    pkg_root.mkdir(exist_ok=True)
    build(pkg_root, init=init, sources=None)
    monkeypatch.syspath_prepend(str(pkg_root))
    for name in [m for m in sys.modules if m.split(".")[0] == "democonn"]:
        monkeypatch.delitem(sys.modules, name)


def route_share_to(monkeypatch: pytest.MonkeyPatch, dav: DavDouble) -> None:
    """Every ``share`` store the exchange builds talks to the WebDAV double."""
    from scion_sdk.exchange import sinks
    from scion_sdk.storage.factory import create_backend as real_create

    def create(store, **kwargs):
        if store.name == "share":
            return WebDavBackend(
                BASE_URL, username="svc", password="pw", transport=dav.transport
            )
        return real_create(store, **kwargs)

    monkeypatch.setattr(sinks, "create_backend", create)
