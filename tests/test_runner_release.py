"""The runner verifies the pinned wheel digest before it provisions (R650.1.3, R650.1.4)."""

from __future__ import annotations

import json
from pathlib import Path

import msgpack
import pytest
from release_fixture import CONNECTOR, KINDS, package, session
from release_support import CONTENT, DISTRIBUTION, EVIDENCE, PINNED, VERSION, FakePins

from scion_sdk.manifest.release_digest import CONTENT_PREFIX
from scion_sdk.ports.release_pins import ReleasePins
from scion_sdk.runner.composition import _release_pins
from scion_sdk.runner.descriptors import RunnerSettings
from scion_sdk.runner.errors import ReleaseVerificationError, RunnerConfigurationError
from scion_sdk.runner.provisioning import provision_content, verify_release
from scion_sdk.runner.release_pins import MedullaReleasePins, release_node_id
from scion_sdk.testing.sinks import InMemorySink


class _Unreachable(ReleasePins):
    async def pinned_digests(self, distribution: str, version: str) -> frozenset[str]:
        raise ConnectionError("engine unreachable")


async def _code(
    content: object, pins: object, *, installed: bool = True
) -> tuple[str, InMemorySink]:
    sink = InMemorySink()
    async with session(content, installed=installed) as opened:  # type: ignore[arg-type]
        with pytest.raises(ReleaseVerificationError) as caught:
            await provision_content(
                opened,
                connector=CONNECTOR,
                kinds=KINDS,
                sink=sink,
                pins=pins,  # type: ignore[arg-type]
            )
    return caught.value.code, sink


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.3")
async def test_each_code_blocks_provisioning(tmp_path: Path) -> None:
    content = package(tmp_path)
    code, sink = await _code(content, PINNED, installed=False)
    assert code == "RELEASE_DIGEST_UNREPORTED"
    code, sink = await _code(content, FakePins())
    assert code == "RELEASE_PIN_MISSING"
    other = FakePins(
        (DISTRIBUTION, VERSION, frozenset({f"{CONTENT_PREFIX}{'cd' * 32}"}))
    )
    code, sink = await _code(content, other)
    assert code == "RELEASE_DIGEST_MISMATCH"
    wheel_only = FakePins((DISTRIBUTION, VERSION, frozenset({f"sha256:{CONTENT}"})))
    code, sink = await _code(content, wheel_only)
    assert (
        code == "RELEASE_DIGEST_MISMATCH"
    )  # only the content digest is the runner's pin
    code, sink = await _code(content, _Unreachable())
    assert code == "RELEASE_PIN_UNAVAILABLE"
    assert sink.packs == {}
    async with session(content) as opened:
        await verify_release(opened, CONNECTOR, PINNED)  # a matching pin passes
    assert EVIDENCE.pin in PINNED.entries[(DISTRIBUTION, VERSION)]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.4")
async def test_pins_come_from_the_caller_or_the_engine_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RunnerConfigurationError):
        _release_pins(None, None)
    assert _release_pins(PINNED, None) is PINNED
    client = object()
    pins = _release_pins(None, client)
    assert isinstance(pins, MedullaReleasePins)
    sent: list[tuple[object, dict[str, str], str | None]] = []
    record = {  # the engine stores the whole record as JSON text in ``release_record``
        "type": "Release",
        "release_record": json.dumps(
            {
                "repo": DISTRIBUTION,
                "version": VERSION,
                "artifact_digests": ["sha256:" + "1" * 64, EVIDENCE.pin],
            }
        ),
    }

    async def send(
        client_: object, params: dict[str, str], graph: str | None
    ) -> object:
        sent.append((client_, params, graph))
        if params["node_id"] == release_node_id(DISTRIBUTION, VERSION):
            return msgpack.packb(record)  # the engine answers MessagePack bytes
        return msgpack.packb({})

    monkeypatch.setattr("medulla.generated.graph.send_get_node_properties", send)
    assert EVIDENCE.pin in await pins.pinned_digests(DISTRIBUTION, VERSION)
    assert sent[0][:2] == (client, {"node_id": f"release:{DISTRIBUTION}:{VERSION}"})
    assert await pins.pinned_digests(DISTRIBUTION, "9.9.9") == frozenset()  # unrecorded
    with pytest.raises(ValueError):
        RunnerSettings(release_lock=tmp_path / "release.lock")  # type: ignore[call-arg]
