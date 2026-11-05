"""SCION-CENTRALIZED-CAPABILITIES-R043: the public exchange surface holds together."""

import pytest

from scion_sdk import exchange
from scion_sdk.exchange.config import (
    EXCHANGE_CONFIG_INVALID,
    EXCHANGE_TEMPLATE_INVALID,
    TEMPLATE_VARIABLES,
    SinkDecl,
)
from scion_sdk.exchange.inputs import (
    EXCHANGE_EGRESS_REFUSED,
    EXCHANGE_INPUT_FAILED,
    EXCHANGE_INPUT_TOO_LARGE,
    EXCHANGE_SINK_UNKNOWN,
    EXCHANGE_URL_INPUT_DISABLED,
    is_input_reference,
)
from scion_sdk.exchange.lineage import EXCHANGE_LINEAGE_FAILED, lineage_change_set
from scion_sdk.exchange.publish import (
    EXCHANGE_OUTPUT_LIMIT,
    EXCHANGE_OUTPUT_TOO_LARGE,
    EXCHANGE_PUBLISH_FAILED,
    PublishContext,
    PublishReport,
    digest_file,
)
from scion_sdk.exchange.results import (
    ARTIFACTS_META_KEY,
    EXCHANGE_META_KEY,
    artifact_link,
)
from scion_sdk.exchange.run import (
    ExchangeCall,
    _Stager,
    exchange_call,
    prepare_arguments,
)
from scion_sdk.exchange.sinks import SinkBackends
from scion_sdk.exchange.staging import (
    EXCHANGE_RUN_ID_INVALID,
    RUN_META_KEY,
    RunStaging,
    input_tree,
    output_tree,
)
from scion_sdk.mcp.upstreams.exchange import ExchangeProxyTool, ExchangeRuntime
from tests.exchange.exchange_support import configure


class _Result:
    content = None
    meta = None


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043")
async def test_exchange_surface_runs_one_call(tmp_path, monkeypatch):
    codes = {
        EXCHANGE_CONFIG_INVALID,
        EXCHANGE_TEMPLATE_INVALID,
        EXCHANGE_EGRESS_REFUSED,
        EXCHANGE_INPUT_FAILED,
        EXCHANGE_INPUT_TOO_LARGE,
        EXCHANGE_SINK_UNKNOWN,
        EXCHANGE_URL_INPUT_DISABLED,
        EXCHANGE_LINEAGE_FAILED,
        EXCHANGE_OUTPUT_LIMIT,
        EXCHANGE_OUTPUT_TOO_LARGE,
        EXCHANGE_PUBLISH_FAILED,
        EXCHANGE_RUN_ID_INVALID,
    }
    assert all(code.startswith("EXCHANGE_") for code in codes) and len(codes) == 12
    assert "name" in TEMPLATE_VARIABLES and RUN_META_KEY == "scion/run"
    assert SinkDecl(name="s", store="d", bucket="b").path_template.endswith("{name}")
    assert is_input_reference("exchange://s/k") and not is_input_reference("out.png")
    settings = configure(tmp_path, monkeypatch)
    assert (
        input_tree(settings.staging_root).name == "in"
        and output_tree(settings.staging_root).name == "out"
    )
    backends = SinkBackends(settings)
    runtime = ExchangeRuntime(settings, backends, "democonn")
    assert issubclass(ExchangeProxyTool, object) and runtime.connector == "democonn"
    staging = RunStaging.open(settings, "app", "run-s")
    prepared = prepare_arguments(
        {"path": "a.txt", "n": 1},
        staging,
        _Stager(staging, settings, backends, None),
        ("path",),
    )
    assert prepared["path"].endswith("run-s/a.txt") and prepared["n"] == 1

    async def call(args):
        (staging.output_dir / "a.txt").write_bytes(b"a")
        return _Result()

    spec = ExchangeCall(
        namespace="app", tool="t", connector="democonn", path_args=("path",)
    )
    result = await exchange_call(
        {"path": "a.txt"},
        call,
        settings=settings,
        backends=backends,
        spec=spec,
        meta={RUN_META_KEY: "run-s"},
    )
    artifacts = result.meta[ARTIFACTS_META_KEY]
    assert artifacts[0]["sha256"] == digest_file(staging.output_dir / "a.txt")
    assert result.meta[EXCHANGE_META_KEY]["run"] == "run-s"
    report = PublishReport(run_id="r")
    assert (
        artifact_link(
            exchange.PublishedArtifact(name="x", sha256="", size=0, media_type="m")
        )
        is None
    )
    assert (
        lineage_change_set(report, [], PublishContext("app", "t")).entities[0].node_type
        == "Action"
    )
