"""SCION-CENTRALIZED-CAPABILITIES-R043.6: lineage records for published artifacts."""

import pytest

from scion_sdk.exchange.lineage import StagedInput, record_lineage
from scion_sdk.exchange.publish import (
    ArtifactLocation,
    PublishContext,
    PublishedArtifact,
    PublishReport,
)


class _Ingest:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def submit(self, binding, change_set):
        if self.fail:
            raise RuntimeError("engine down")
        self.calls.append((binding, change_set))


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R043.6")
async def test_lineage_change_set_links_outputs_inputs_and_run():
    report = PublishReport(
        run_id="run-1",
        artifacts=[
            PublishedArtifact(
                name="out.png",
                sha256="aa" * 32,
                size=3,
                media_type="image/png",
                locations=(ArtifactLocation(sink="files", key="k"),),
            )
        ],
    )
    inputs = [StagedInput("exchange://files/in/a.png", "a.png", "bb" * 32, 2)]
    context = PublishContext("photocraft", "doc_save", "user-1")
    ingest = _Ingest()
    assert (
        await record_lineage(report, inputs, context, connector="demo", ingest=ingest)
        is None
    )
    binding, change_set = ingest.calls[0]
    assert binding.stream == "artifacts"
    kinds = {(r.source, r.relationship, r.target) for r in change_set.relationships}
    run, out, src = (
        "run:photocraft:run-1",
        f"artifact:sha256:{'aa' * 32}",
        f"artifact:sha256:{'bb' * 32}",
    )
    assert {
        (out, "participatedIn", run),
        (run, "hasInput", src),
        (out, "derivedFrom", src),
    } <= kinds
    action = next(e for e in change_set.entities if e.id == run)
    assert action.node_type == "Action" and action.properties["caller"] == "user-1"
    assert (
        await record_lineage(
            report, inputs, context, connector="demo", ingest=_Ingest(fail=True)
        )
        == "EXCHANGE_LINEAGE_FAILED"
    )
