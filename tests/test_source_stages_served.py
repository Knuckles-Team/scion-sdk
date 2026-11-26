"""The served journey of concurrent stages: kill one stage, restart, resume (R001)."""

from __future__ import annotations

import dataclasses
from typing import Any

import anyio
import pytest
from runner_support import services
from stage_support import CONNECTOR, TENANT, StageAdapter, limits

from scion_sdk.runner import worker as worker_module
from scion_sdk.runner.descriptors import ConnectorDescriptor
from scion_sdk.runner.errors import StageCycleFailed
from scion_sdk.runner.plans import full_plan
from scion_sdk.testing.sinks import InMemorySink


@pytest.mark.spec("SCION-SOURCE-INGEST-R001")
@pytest.mark.spec("SCION-SOURCE-INGEST-R001.6")
async def test_concurrent_stage_restart() -> None:
    sink = InMemorySink()
    descriptor = ConnectorDescriptor.model_validate(
        {
            "connector": CONNECTOR,
            "endpoint": {"url": "http://127.0.0.1:9/mcp"},
            "provision": False,
            "tenant": TENANT,
            "stage_limits": limits().model_dump(),
            "stages": [
                {"stage_id": "a", "preset": "a", "stream": "a"},
                {"stage_id": "b", "preset": "b", "stream": "b"},
            ],
        }
    )
    overlap = {"a": anyio.Event(), "b": anyio.Event()}

    async def meet(adapter: StageAdapter) -> None:
        overlap[adapter.stream].set()
        with anyio.fail_after(5):
            await overlap["b" if adapter.stream == "a" else "a"].wait()

    killed = {"on": True}

    async def die(adapter: StageAdapter) -> None:
        await meet(adapter)
        if killed["on"]:
            raise RuntimeError("stage killed")

    adapters: Any = {"a": StageAdapter("a", meet), "b": StageAdapter("b", die)}
    worker = worker_module.ConnectorWorker(
        descriptor,
        dataclasses.replace(services(sink, lambda d: None), health=None),  # type: ignore[arg-type,return-value]
        anyio.CapacityLimiter(1),
    )
    plan = full_plan(descriptor, adapters)
    with pytest.raises(StageCycleFailed):
        await worker._cycle(None, adapters, plan)  # type: ignore[arg-type]
    committed = (await sink.source_status(CONNECTOR, "a")).accepted_checkpoint
    assert committed is not None
    assert (await sink.source_status(CONNECTOR, "b")).accepted_checkpoint is None
    killed["on"] = False
    overlap["a"], overlap["b"] = anyio.Event(), anyio.Event()
    await worker._cycle(None, adapters, plan)  # type: ignore[arg-type]
    assert adapters["a"].seen[1] == committed
    assert adapters["b"].seen[1] is None
    assert len(sink.batches) == 2
