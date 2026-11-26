"""``connector-release-sign`` writes verifiable evidence and refuses bad keys (R650.1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from release_fixture import CONNECTOR, package, server
from release_support import KEY_ID, TRUSTED, _Resolver, write_lock

from scion_sdk.certify import release_cli
from scion_sdk.manifest.release_lock import create_release_key_lock
from scion_sdk.ports.session import TransportEndpoint
from scion_sdk.runner.provisioning import verify_release
from scion_sdk.transports.mcp import McpTransport


def _arguments(root: Path, lock: Path, key_ref: str) -> list[str]:
    return [
        "--package-root", str(root), "--connector", CONNECTOR,
        "--key-id", KEY_ID, "--key-ref", key_ref, "--lock", str(lock),
        "--", "unused-server-command",
    ]  # fmt: skip


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    content = package(tmp_path / "pkg")
    monkeypatch.setattr(
        release_cli,
        "build_endpoint",
        lambda _args: TransportEndpoint(in_process=server(content)),
    )
    monkeypatch.setattr(release_cli, "default_credential_resolver", _Resolver)
    return tmp_path / "pkg"


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R650.1.2")
async def test_sign_writes_verifiable_evidence_and_refuses_untrusted_key(
    served: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from functools import partial

    import anyio

    key_ref = f"openbao://apps/{CONNECTOR}/sign#seed@1"
    lock = write_lock(tmp_path / "good.lock")
    code = await anyio.to_thread.run_sync(
        partial(release_cli.main, _arguments(served, lock, key_ref))
    )
    assert code == 0
    written = (served / "release.json").read_text(encoding="utf-8")
    assert set(__import__("json").loads(written)) == {"key_id", "digest", "signature"}
    content = package(served)
    async with McpTransport().session(
        TransportEndpoint(in_process=server(content))
    ) as session:
        await verify_release(session, CONNECTOR, TRUSTED)
    (served / "release.json").unlink()
    other = Path(tmp_path / "other.lock")
    create_release_key_lock(other, {"artifact": (KEY_ID, bytes(32))})
    for lock_file, ref in (
        (other, key_ref),
        (lock, f"openbao://apps/{CONNECTOR}/sign#seed"),
    ):
        code = await anyio.to_thread.run_sync(
            partial(release_cli.main, _arguments(served, lock_file, ref))
        )
        assert code == 2 and not (served / "release.json").exists()
