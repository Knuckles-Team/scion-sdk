"""The release evidence resource is served and never packed (R650.1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP
from release_fixture import KINDS, package, session, sign_package

from scion_sdk.artifacts.pack import build_content_pack
from scion_sdk.manifest.release_signature import (
    ReleaseSigningError,
    read_release_evidence,
)
from scion_sdk.mcp.content import RELEASE_URI, ContentError, register_release_resource


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.1")
async def test_release_resource_is_served_and_not_packed(tmp_path: Path) -> None:
    content = package(tmp_path)
    assert content.release_path == tmp_path / "release.json"
    async with session(content) as opened:
        uris = {str(r.uri) for r in await opened.list_resources()}
    assert RELEASE_URI not in uris  # no file, no resource
    await sign_package(content)
    async with session(content) as opened:
        uris = {str(r.uri) for r in await opened.list_resources()}
        assert RELEASE_URI in uris
        evidence = read_release_evidence(await opened.read_resource(RELEASE_URI))
        pack = await build_content_pack(opened, connector="demo-agent", kinds=KINDS)
    assert evidence.key_id and RELEASE_URI not in pack.resource_uris
    with pytest.raises(ReleaseSigningError):
        read_release_evidence(
            '{"key_id": "k", "digest": "d", "signature": "s", "x": 1}'
        )
    content.release_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ContentError):
        register_release_resource(FastMCP("x"), content)
    content.release_path.unlink()
    assert register_release_resource(FastMCP("x"), content) is False
