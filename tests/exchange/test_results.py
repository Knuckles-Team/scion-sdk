"""SCION-CENTRALIZED-CAPABILITIES-R043.5: tool results carry resource links."""

import json

import pytest
from mcp_types import TextContent

from scion_sdk.exchange.publish import (
    ArtifactLocation,
    PublishedArtifact,
    PublishReport,
)
from scion_sdk.exchange.results import attach_artifacts


class _Result:
    def __init__(self):
        self.content = [TextContent(type="text", text="saved")]
        self.meta = None


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.5")
def test_links_follow_upstream_content_and_hold_no_secret():
    report = PublishReport(
        run_id="run-1",
        artifacts=[
            PublishedArtifact(
                name="out.png",
                sha256="ab" * 32,
                size=3,
                media_type="image/png",
                locations=(
                    ArtifactLocation(
                        sink="objects",
                        key="p/out.png",
                        url="https://objects.example/p/out.png?X-Sig=1",
                    ),
                    ArtifactLocation(sink="files", key="p/out.png"),
                ),
            ),
            PublishedArtifact(
                name="lost.bin",
                sha256="",
                size=1,
                media_type="application/octet-stream",
                code="EXCHANGE_OUTPUT_LIMIT",
            ),
        ],
        ok=False,
    )
    result = attach_artifacts(_Result(), report)
    assert result.content[0].text == "saved"
    links = [block for block in result.content if block.type == "resource_link"]
    assert len(links) == 1 and links[0].uri.startswith("https://objects.example/")
    assert result.meta["scion/exchange"]["codes"] == ["EXCHANGE_PUBLISH_FAILED"]
    assert len(result.meta["scion/artifacts"]) == 2
    assert "dav-secret-value" not in json.dumps(result.meta)
