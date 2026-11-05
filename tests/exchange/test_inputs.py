"""SCION-CENTRALIZED-CAPABILITIES-R043.3: input resolution from sinks and URLs."""

import httpx
import pytest

from scion_sdk.exchange.inputs import ExchangeInputError, resolve_input
from scion_sdk.exchange.staging import RunStaging
from tests.exchange.exchange_support import backends_with_dav, configure
from tests.storage.dav_double import DavDouble


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.3")
def test_sink_and_url_inputs_stage_and_refuse_unsafe(tmp_path, monkeypatch):
    settings = configure(tmp_path, monkeypatch, SCION_EXCHANGE_MAX_INPUT_BYTES="16")
    dav = DavDouble()
    dav.collections.update({"ArtCraft", "ArtCraft/in"})
    dav.files["ArtCraft/in/photo.png"] = b"png-bytes"
    backends = backends_with_dav(settings, dav)
    staging = RunStaging.open(settings, "app", "run-1")

    staged = resolve_input("exchange://files/in/photo.png", staging, settings, backends)
    assert staged is not None and staged.parent == staging.input_dir
    assert staged.read_bytes() == b"png-bytes"
    assert resolve_input("relative/out.png", staging, settings, backends) is None

    with pytest.raises(ExchangeInputError) as excinfo:
        resolve_input("exchange://nowhere/x.png", staging, settings, backends)
    assert excinfo.value.code == "EXCHANGE_SINK_UNKNOWN"
    for url in ("https://10.0.0.5/x.png", "http://files.example/x.png"):
        with pytest.raises(ExchangeInputError) as excinfo:
            resolve_input(url, staging, settings, backends)
        assert excinfo.value.code == "EXCHANGE_EGRESS_REFUSED"

    big = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 64))
    monkeypatch.setattr(
        "scion_sdk.exchange.inputs.validate_base_url_resolved",
        lambda *a, **k: type("D", (), {"allowed": True})(),
    )
    with pytest.raises(ExchangeInputError) as excinfo:
        resolve_input(
            "https://cdn.example/big.bin", staging, settings, backends, transport=big
        )
    assert excinfo.value.code == "EXCHANGE_INPUT_TOO_LARGE"
    small = httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok"))
    fetched = resolve_input(
        "https://cdn.example/a/b.txt", staging, settings, backends, transport=small
    )
    assert (
        fetched is not None
        and fetched.read_bytes() == b"ok"
        and fetched.name == "b.txt"
    )
