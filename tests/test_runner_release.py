"""The runner verifies the signed release before it provisions (R650.1.3, R650.1.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from release_fixture import CONNECTOR, KINDS, package, session, sign_package
from release_support import KEY_ID, TRUSTED

from scion_sdk.manifest.release_signature import ReleaseSigningError
from scion_sdk.runner.composition import default_services
from scion_sdk.runner.descriptors import RunnerSettings
from scion_sdk.runner.errors import ReleaseVerificationError
from scion_sdk.runner.provisioning import provision_content
from scion_sdk.testing.sinks import InMemorySink


async def _code(content: object, keys: dict[str, bytes]) -> tuple[str, InMemorySink]:
    sink = InMemorySink()
    async with session(content) as opened:  # type: ignore[arg-type]
        with pytest.raises(ReleaseVerificationError) as caught:
            await provision_content(
                opened, connector=CONNECTOR, kinds=KINDS, sink=sink, trusted_keys=keys
            )
    return caught.value.code, sink


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.3")
async def test_each_code_blocks_provisioning(tmp_path: Path) -> None:
    content = package(tmp_path)
    code, sink = await _code(content, TRUSTED)
    assert code == "RELEASE_UNSIGNED"
    await sign_package(content)
    code, sink = await _code(content, {"other": TRUSTED[KEY_ID]})
    assert code == "RELEASE_KEY_UNTRUSTED"
    ontology = tmp_path / "ontology" / "demo.ttl"
    ontology.write_text(ontology.read_text() + "<urn:a> a <urn:B> .\n")
    code, sink = await _code(content, TRUSTED)
    assert code == "RELEASE_DIGEST_MISMATCH"
    ontology.write_text(ontology.read_text().replace("<urn:a> a <urn:B> .\n", ""))
    evidence = json.loads(content.release_path.read_text())
    evidence["signature"] = evidence["signature"][::-1]
    content.release_path.write_text(json.dumps(evidence))
    code, sink = await _code(content, TRUSTED)
    assert code == "RELEASE_SIGNATURE_INVALID"
    assert sink.packs == {}


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.4")
def test_startup_requires_a_readable_lock(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        RunnerSettings()  # type: ignore[call-arg]
    for body in (None, "not: [valid"):
        lock = tmp_path / "bad.lock"
        if body is not None:
            lock.write_text(body)
        with pytest.raises(ReleaseSigningError):
            default_services(
                RunnerSettings(release_lock=lock),
                state_dir=tmp_path,
                sink_name="medulla",
                sink_client=object(),
            )
