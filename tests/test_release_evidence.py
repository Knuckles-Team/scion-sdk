"""The wheel content digest is served from the installed wheel and never packed (R650.1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import FastMCP
from release_fixture import KINDS, package, session
from release_support import EVIDENCE

from scion_sdk.artifacts.pack import build_content_pack
from scion_sdk.manifest.release_digest import ReleaseDigestError, read_release_evidence
from scion_sdk.mcp.content import RELEASE_URI, register_release_resource


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.1")
async def test_release_resource_is_served_and_not_packed(tmp_path: Path) -> None:
    content = package(tmp_path)
    async with session(content, installed=False) as opened:
        uris = {str(r.uri) for r in await opened.list_resources()}
    assert RELEASE_URI not in uris  # no installed wheel, no resource
    async with session(content) as opened:
        uris = {str(r.uri) for r in await opened.list_resources()}
        assert RELEASE_URI in uris
        evidence = read_release_evidence(await opened.read_resource(RELEASE_URI))
        pack = await build_content_pack(opened, connector="demo-agent", kinds=KINDS)
    assert evidence == EVIDENCE and RELEASE_URI not in pack.resource_uris
    assert not (tmp_path / "release.json").exists()  # nothing ships in the wheel
    for body in (
        '{"distribution": "d", "version": "1", "content_sha256": "x"}',
        '{"distribution": "d", "version": "1", "content_sha256": "'
        + "a" * 64
        + '", "x": 1}',
        "not json",
    ):
        with pytest.raises(ReleaseDigestError):
            read_release_evidence(body)
    content_only = FastMCP("x")
    assert register_release_resource(
        content_only, content, evidence=lambda _d: EVIDENCE
    )
    assert await content_only.list_resources()
