"""Hot path: the runner refuses an unpinned or altered wheel (R650, R650.1, R650.1.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from release_fixture import CONNECTOR, KINDS, package, session
from release_support import DISTRIBUTION, PINNED, VERSION, FakePins

from scion_sdk.runner.errors import ReleaseVerificationError
from scion_sdk.runner.provisioning import provision_content
from scion_sdk.testing.sinks import InMemorySink


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.5")
async def test_runner_refuses_unpinned_and_altered_release(tmp_path: Path) -> None:
    content = package(tmp_path)
    sink = InMemorySink()
    async with session(content) as opened:
        outcome = await provision_content(
            opened, connector=CONNECTOR, kinds=KINDS, sink=sink, pins=PINNED
        )
    assert outcome.changed and PINNED.reads >= 1
    altered = FakePins(
        (DISTRIBUTION, VERSION, frozenset({"content-sha256:" + "00" * 32}))
    )
    refused = InMemorySink()
    async with session(content) as opened:
        with pytest.raises(ReleaseVerificationError) as caught:
            await provision_content(
                opened, connector=CONNECTOR, kinds=KINDS, sink=refused, pins=altered
            )
    assert caught.value.code == "RELEASE_DIGEST_MISMATCH" and refused.packs == {}
    unpinned = InMemorySink()
    async with session(content) as opened:
        with pytest.raises(ReleaseVerificationError) as caught:
            await provision_content(
                opened, connector=CONNECTOR, kinds=KINDS, sink=unpinned, pins=FakePins()
            )
    assert caught.value.code == "RELEASE_PIN_MISSING" and unpinned.packs == {}
