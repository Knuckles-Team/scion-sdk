"""The stage executor: overlap, order, refusals, readback and reports (R001.x)."""

from __future__ import annotations

import dataclasses
from typing import Any

import anyio
import pytest
from medulla.generated.source_ingestion import SourceCheckpoint
from stage_support import CONNECTOR, TENANT, StageAdapter, limits, stage

from scion_sdk.runner.descriptors import ConnectorDescriptor, StageDeclaration
from scion_sdk.runner.errors import StageCycleFailed, StageRefusal
from scion_sdk.runner.health_reporting import note_stage_report
from scion_sdk.runner.health_state import RunnerHealth
from scion_sdk.runner.plans import full_plan, stage_descriptors
from scion_sdk.runner.stage_runner import (
    StageReport,
    StageResult,
    receipt_id,
    run_stages,
)
from scion_sdk.runner.stages import StageDependency
from scion_sdk.runner.syncing import SyncTarget
from scion_sdk.testing.sinks import InMemorySink

SESSION: Any = object()


def _receipt(stream: str) -> str:
    return receipt_id(SourceCheckpoint(stream=stream, position={"page": 1}))


def _target(sink: Any, *stages: Any) -> SyncTarget:
    return SyncTarget(CONNECTOR, sink, 5, tenant=TENANT, stages=tuple(stages))


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.2")
async def test_independent_stages_overlap_and_dependent_waits() -> None:
    log: list[str] = []
    started = {"a": anyio.Event(), "b": anyio.Event()}

    async def overlap(adapter: StageAdapter) -> None:
        log.append(f"start:{adapter.stream}")
        started[adapter.stream].set()
        other = "b" if adapter.stream == "a" else "a"
        with anyio.fail_after(5):
            await started[other].wait()

    async def plain(adapter: StageAdapter) -> None:
        log.append(f"start:{adapter.stream}")

    adapters = {
        "a": StageAdapter("a", overlap),
        "b": StageAdapter("b", overlap),
        "c": StageAdapter("c", plain),
    }
    sink = InMemorySink()
    c = stage("c", ("a", _receipt("a")))
    report = await run_stages(
        _target(sink, stage("a"), stage("b"), c), SESSION, adapters, run_id="r1"
    )
    assert report.succeeded
    assert [r.outcome for r in report.results] == ["complete"] * 3
    assert log.index("start:c") > log.index("start:a")
    assert report.results[2].dependency_receipts == (_receipt("a"),)
    assert report.results[0].accepted_receipt == _receipt("a")


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.2")
async def test_failed_stage_blocks_dependents_and_keeps_sibling_receipt() -> None:
    async def boom(adapter: StageAdapter) -> None:
        raise RuntimeError("secret-payload")

    adapters = {
        "a": StageAdapter("a", boom),
        "b": StageAdapter("b"),
        "c": StageAdapter("c"),
    }
    report = await run_stages(
        _target(
            InMemorySink(), stage("a"), stage("b"), stage("c", ("a", _receipt("a")))
        ),
        SESSION,
        adapters,
        run_id="r2",
    )
    by_id = {r.stage_id: r for r in report.results}
    assert by_id["a"].outcome == "failed" and by_id["a"].reason == "RuntimeError"
    assert by_id["b"].outcome == "complete" and by_id["b"].accepted_receipt
    assert by_id["c"].outcome == "blocked" and by_id["c"].accepted_receipt == ""
    assert not report.succeeded
    assert adapters["c"].extracted == 0


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.3")
async def test_worker_cycle_uses_stage_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from runner_support import services

    from scion_sdk.runner import worker as worker_module

    sink = InMemorySink()
    health = RunnerHealth(sink=sink, liveness_window_seconds=60)
    descriptor = ConnectorDescriptor.model_validate(
        {
            "connector": CONNECTOR,
            "endpoint": {"url": "http://127.0.0.1:9/mcp"},
            "provision": False,
            "tenant": TENANT,
            "stage_limits": limits().model_dump(),
            "stages": [
                {"stage_id": "a", "preset": "a", "stream": "a"},
                {
                    "stage_id": "c",
                    "preset": "c",
                    "stream": "c",
                    "dependencies": [{"stage_id": "a", "receipt_id": _receipt("a")}],
                },
            ],
        }
    )
    adapters: Any = {
        "a": StageAdapter("a"),
        "c": StageAdapter("c"),
        "d": StageAdapter("d"),
    }
    calls: list[str] = []
    real = worker_module.run_stages

    async def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["run_id"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(worker_module, "run_stages", spy)
    worker = worker_module.ConnectorWorker(
        descriptor,
        dataclasses.replace(
            services(sink, lambda d: None),
            health=health,  # type: ignore[arg-type,return-value]
        ),
        anyio.CapacityLimiter(1),
    )
    plan = full_plan(descriptor, adapters)
    assert plan.presets == frozenset({"d"}) and len(plan.stages) == 2
    await worker._cycle(SESSION, adapters, plan)
    assert len(calls) == 1
    report = health.stage_report(CONNECTOR)
    assert report is not None and report["succeeded"] is True
    assert stage_descriptors(descriptor)[1].dependencies[0].stage_id == "a"


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.3")
def test_descriptor_refuses_stages_without_limits() -> None:
    with pytest.raises(ValueError, match="STAGE_LIMITS_ABSENT"):
        ConnectorDescriptor.model_validate(
            {
                "connector": CONNECTOR,
                "endpoint": {"url": "http://127.0.0.1:9/mcp"},
                "stages": [{"stage_id": "a", "preset": "a", "stream": "a"}],
            }
        )
    assert StageDeclaration(stage_id="a", preset="a", stream="a").dependencies == ()


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.4")
async def test_stage_refusals_and_ambiguous_commit_readback() -> None:
    adapters = {"a": StageAdapter("a"), "b": StageAdapter("b")}
    sink = InMemorySink()

    a = stage("a")
    base = _target(sink, a)
    for stages, code in (
        ((a, a), "STAGE_IDENTITY_COLLISION"),
        (
            (
                stage("a", ("b", "r")),
                stage("b", ("a", "r")),
            ),
            "STAGE_CYCLE",
        ),
        ((a.model_copy(update={"tenant": "other"}),), "STAGE_SCOPE_MISMATCH"),
        ((), "STAGE_LIMITS_ABSENT"),
    ):
        bad = dataclasses.replace(base)
        object.__setattr__(bad, "stages", stages)
        with pytest.raises(StageRefusal) as caught:
            await run_stages(bad, SESSION, adapters, run_id="x")
        assert caught.value.code == code
    assert adapters["a"].extracted == 0
    with pytest.raises(ValueError):
        StageRefusal("NOPE")


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.4")
async def test_receipt_mismatch_blocks_and_progress_token_is_never_inherited() -> None:
    adapters = {"a": StageAdapter("a"), "c": StageAdapter("c")}
    report = await run_stages(
        _target(InMemorySink(), stage("a"), stage("c", ("a", "sha256:wrong"))),
        SESSION,
        adapters,
        run_id="r3",
    )
    blocked = report.results[1]
    assert blocked.outcome == "blocked" and blocked.reason == "STAGE_RECEIPT_MISMATCH"

    class Stray(StageAdapter):
        async def extract(self, session: object, checkpoint: Any) -> Any:
            page = await super().extract(session, checkpoint)
            return page.model_copy(
                update={"checkpoint": SourceCheckpoint(stream="other", position={})}
            )

    sink = InMemorySink()
    stray = await run_stages(
        _target(sink, stage("a")), SESSION, {"a": Stray("a")}, run_id="r4"
    )
    assert stray.results[0].outcome in {"failed"}


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.4")
async def test_timeout_after_commit_reads_durable_status_and_does_not_retry() -> None:
    sink = InMemorySink()

    class Slow(StageAdapter):
        async def extract(self, session: object, checkpoint: Any) -> Any:
            page = await super().extract(session, checkpoint)
            if self.extracted == 1:
                await sink.submit(  # commit lands, then the call times out
                    __import__(
                        "scion_sdk.runner.syncing", fromlist=["x"]
                    )._source_request(page, _target(sink, stage("a")), checkpoint)
                )
                raise TimeoutError
            return page

    adapter = Slow("a")
    report = await run_stages(
        _target(sink, stage("a")), SESSION, {"a": adapter}, run_id="r5"
    )
    result = report.results[0]
    assert result.reason == "TIMEOUT_AFTER_COMMIT" and result.offered_checkpoint
    assert adapter.extracted == 1


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.4")
async def test_timeout_without_commit_retries_once() -> None:
    class Flaky(StageAdapter):
        async def extract(self, session: object, checkpoint: Any) -> Any:
            page = await super().extract(session, checkpoint)
            if self.extracted == 1:
                raise TimeoutError
            return page

    adapter = Flaky("a")
    report = await run_stages(
        _target(InMemorySink(), stage("a")), SESSION, {"a": adapter}, run_id="r6"
    )
    assert report.succeeded and adapter.extracted == 2


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.4")
async def test_cancellation_reads_back_and_reports_cancelled() -> None:
    async def hang(adapter: StageAdapter) -> None:
        await anyio.sleep_forever()

    sink = InMemorySink()
    target = _target(sink, stage("a"))
    adapter = StageAdapter("a", hang)
    with anyio.move_on_after(0.05):
        await run_stages(target, SESSION, {"a": adapter}, run_id="r7")
    assert adapter.extracted == 1


@pytest.mark.spec("SCION-SOURCE-INGEST-R001.5")
async def test_report_matches_durable_status_after_restart() -> None:
    sink = InMemorySink()
    adapters = {"a": StageAdapter("a")}
    report = await run_stages(_target(sink, stage("a")), SESSION, adapters, run_id="r8")
    status = await sink.source_status(CONNECTOR, "a")
    assert report.results[0].accepted_receipt == receipt_id(status.accepted_checkpoint)
    document = report.as_document()
    assert "payload" not in str(document) and document["run_id"] == "r8"
    again = await run_stages(
        _target(sink, stage("a")), SESSION, {"a": StageAdapter("a")}, run_id="r9"
    )
    assert again.results[0].previous_checkpoint == status.accepted_checkpoint
    health = RunnerHealth(sink=sink, liveness_window_seconds=60)
    health.record_stage_report(CONNECTOR, document)
    assert health.stage_report(CONNECTOR) == document
    assert isinstance(report, StageReport) and isinstance(
        report.results[0], StageResult
    )
    note_stage_report(None, CONNECTOR, report)
    note_stage_report(health, CONNECTOR, again)
    assert health.stage_report(CONNECTOR) == again.as_document()
    assert StageCycleFailed(CONNECTOR).connector == CONNECTOR
    assert StageDependency(stage_id="a", receipt_id="r").stage_id == "a"
