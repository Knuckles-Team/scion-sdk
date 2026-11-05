"""SCION-CENTRALIZED-CAPABILITIES-R042.8: the exchange sinks write through the shared backends."""

import pytest

from scion_sdk.exchange.publish import PublishContext, publish_outputs
from scion_sdk.exchange.sinks import SinkBackends
from scion_sdk.exchange.staging import RunStaging
from scion_sdk.storage.filesystem import FilesystemBackend
from scion_sdk.storage.webdav import WebDavBackend
from tests.exchange.exchange_support import backends_with_dav, configure
from tests.storage.dav_double import DavDouble


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.8")
def test_exchange_sink_writes_through_filesystem_and_webdav_backends(
    tmp_path, monkeypatch
):
    settings = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "scion_sdk.storage.factory.validate_base_url_resolved",
        lambda *a, **k: type("Decision", (), {"allowed": True})(),
    )
    built = SinkBackends(settings)
    assert isinstance(built.backend("disk"), FilesystemBackend)
    assert isinstance(built.backend("share"), WebDavBackend)
    dav = DavDouble()
    backends = backends_with_dav(settings, dav)
    staging = RunStaging.open(settings, "app", "run-9")
    (staging.output_dir / "a.txt").write_bytes(b"a")
    report = publish_outputs(staging, settings, backends, PublishContext("app", "tool"))
    locations = report.artifacts[0].locations
    assert all(loc.code is None for loc in locations) and len(locations) == 2
    assert any(path.endswith("run-9/a.txt") for path in dav.files)
