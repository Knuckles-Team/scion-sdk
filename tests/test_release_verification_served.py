"""Hot path: the runner refuses unsigned and tampered releases (R650, R650.1, R650.1.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from release_fixture import CONNECTOR, KINDS, package, session, sign_package
from release_support import TRUSTED

from scion_sdk.runner.errors import ReleaseVerificationError
from scion_sdk.runner.provisioning import provision_content
from scion_sdk.testing.sinks import InMemorySink


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.5")
async def test_runner_refuses_unsigned_and_tampered_release(tmp_path: Path) -> None:
    content = package(tmp_path)
    await sign_package(content)
    sink = InMemorySink()
    async with session(content) as opened:
        outcome = await provision_content(
            opened, connector=CONNECTOR, kinds=KINDS, sink=sink, trusted_keys=TRUSTED
        )
    assert outcome.changed
    ontology = tmp_path / "ontology" / "demo.ttl"
    original = ontology.read_text()
    ontology.write_text(original + "<urn:s> <urn:p> <urn:o> .\n")
    async with session(content) as opened:
        with pytest.raises(ReleaseVerificationError) as caught:
            await provision_content(
                opened,
                connector=CONNECTOR,
                kinds=KINDS,
                sink=InMemorySink(),
                trusted_keys=TRUSTED,
            )
    assert caught.value.code == "RELEASE_DIGEST_MISMATCH"
    ontology.write_text(original)
    content.release_path.unlink()
    unsigned = InMemorySink()
    async with session(content) as opened:
        with pytest.raises(ReleaseVerificationError) as caught:
            await provision_content(
                opened,
                connector=CONNECTOR,
                kinds=KINDS,
                sink=unsigned,
                trusted_keys=TRUSTED,
            )
    assert caught.value.code == "RELEASE_UNSIGNED"
