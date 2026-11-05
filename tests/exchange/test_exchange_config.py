"""SCION-CENTRALIZED-CAPABILITIES-R043.1: exchange settings and sinks."""

import pytest

from scion_sdk.exchange.config import (
    ExchangeConfigError,
    read_exchange_settings,
    render_key,
)
from tests.exchange.exchange_support import EXCHANGE_ENV, configure, exchange_document


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.1")
def test_settings_parse_and_template_refusals(tmp_path, monkeypatch):
    for name in EXCHANGE_ENV:
        monkeypatch.delenv(name, raising=False)
    assert read_exchange_settings() is None

    settings = configure(tmp_path, monkeypatch, SCION_EXCHANGE_MAX_OUTPUTS="3")
    assert settings.max_outputs == 3 and settings.max_input_bytes == 268_435_456
    files = settings.sink("files")
    assert (
        render_key(
            files,
            namespace="photocraft",
            tool="doc_save",
            run="r1",
            name="out.png",
            date="2026-11-04",
        )
        == "photocraft/2026-11-04/r1/out.png"
    )

    bad_variable = exchange_document(tmp_path, path_template="{secret}/{name}")
    with pytest.raises(ExchangeConfigError) as excinfo:
        configure(tmp_path, monkeypatch, bad_variable)
    assert excinfo.value.code == "EXCHANGE_TEMPLATE_INVALID"

    bad_store = exchange_document(tmp_path, store="nowhere")
    with pytest.raises(ExchangeConfigError) as excinfo:
        configure(tmp_path, monkeypatch, bad_store)
    assert excinfo.value.code == "EXCHANGE_CONFIG_INVALID"
