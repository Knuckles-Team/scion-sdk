"""SCION-CENTRALIZED-CAPABILITIES-R043.4: output publication to every sink."""

import pytest

from scion_sdk.exchange.publish import PublishContext, publish_outputs
from scion_sdk.exchange.staging import RunStaging
from scion_sdk.storage.base import StorageError
from tests.exchange.exchange_support import backends_with_dav, configure
from tests.storage.dav_double import DavDouble


class _Failing:
    backend_type = "failing"

    def capabilities(self):
        return {}

    def put_object(self, *args, **kwargs):
        raise StorageError("down")


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.4")
def test_outputs_reach_every_sink_with_digest_and_limits(tmp_path, monkeypatch):
    settings = configure(tmp_path, monkeypatch, SCION_EXCHANGE_MAX_OUTPUT_BYTES="8")
    dav = DavDouble()
    backends = backends_with_dav(settings, dav)
    staging = RunStaging.open(settings, "photocraft", "run-1")
    (staging.output_dir / "out.png").write_bytes(b"png")
    (staging.output_dir / "huge.bin").write_bytes(b"x" * 32)
    report = publish_outputs(
        staging, settings, backends, PublishContext("photocraft", "doc_save")
    )
    assert report.ok
    by_name = {a.name: a for a in report.artifacts}
    assert by_name["huge.bin"].code == "EXCHANGE_OUTPUT_TOO_LARGE"
    artifact = by_name["out.png"]
    assert artifact.sha256 and artifact.media_type == "image/png"
    keys = {loc.sink: loc.key for loc in artifact.locations}
    date = keys["files"].split("/")[1]
    assert keys["files"] == f"photocraft/{date}/run-1/out.png"
    assert dav.files[f"ArtCraft/photocraft/{date}/run-1/out.png"] == b"png"
    disk = tmp_path / "disk" / "artifacts" / "photocraft" / date / "run-1" / "out.png"
    assert disk.read_bytes() == b"png"

    backends.put("share", _Failing())
    (staging.output_dir / "second.png").write_bytes(b"p2")
    report = publish_outputs(
        staging, settings, backends, PublishContext("photocraft", "doc_save")
    )
    assert report.ok  # the failing sink is optional
    second = next(a for a in report.artifacts if a.name == "second.png")
    assert {loc.sink: loc.code for loc in second.locations}[
        "files"
    ] == "EXCHANGE_PUBLISH_FAILED"
