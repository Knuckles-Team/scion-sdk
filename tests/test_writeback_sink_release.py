"""A sink executes only a change set the engine released (SCION-GOVERNED-WRITEBACK-R006).

The queue, the approval and the release run on a real engine through
``tests/live_engine.py``. The pinned engine does not carry the two-person rule
(``WRITEBACK_SELF_APPROVAL``), so a self-approval cannot be asserted here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from live_engine import (
    _LIVE,
    _SECRET,
    GRAPH,
    TENANT,
    _context,
    live_ingest,  # noqa: F401 - pytest fixture
    scoped_client,
)
from medulla import MedullaClient

from scion_sdk.decide.transport import DecideUnavailable, GeneratedTransport
from scion_sdk.writeback_sinks import core, runner
from scion_sdk.writeback_sinks.release import (
    RELEASE_ID_MISSING,
    RELEASE_NOT_FOUND,
    RELEASE_UNAPPROVED,
    RELEASE_UNAVAILABLE,
    ReleasedChangeSet,
    ReleaseError,
    fetch_release,
    release_audit,
)

DOMAIN = "releasefake"
QUEUER = "service:queuer"


class _VendorSink:
    domain = DOMAIN
    enable_flag = "RELEASEFAKE_ENABLE_WRITE"
    risk_tier = "high_stakes"

    def __init__(self) -> None:
        self.live_calls = 0

    def run(self, ctx: Any, ops: dict[str, Any], *, dry_run: bool) -> Any:
        self.live_calls += 0 if dry_run else 1
        result = core.WritebackResult(target=DOMAIN)
        result.created = 0 if dry_run else len(ops.get("creations") or [])
        return result


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    sink = _VendorSink()
    core.register_sink(sink)  # type: ignore[arg-type]
    monkeypatch.setattr(runner, "setting", lambda k, d=None, cast=None: True)
    monkeypatch.setattr(
        "scion_sdk.writeback_sinks.governed.ledger_root", lambda: tmp_path / "ledger"
    )
    yield sink
    core._SINKS.pop(DOMAIN, None)


async def _as(principal: str) -> GeneratedTransport:
    context = {
        **_context(["connector"], ["*"]),
        "principal": principal,
        "agent_id": principal,
    }
    client = await MedullaClient.connect(
        socket_path=str(_LIVE["socket"]),
        auth_secret=_SECRET,
        graph_name=GRAPH,
        verified_context=context,
    )
    return GeneratedTransport(client, GRAPH)


def _op(computation: dict[str, Any]) -> dict[str, Any]:
    return {"op": "writeback", "tenant_id": TENANT, "computation": computation}


def _queue(queue_id: str) -> dict[str, Any]:
    proposal = {
        "id": f"wp:{queue_id}",
        "external_id": "ext:1",
        "system_of_record": "sor",
        "risk_tier": "high",
        "as_of_ms": 1_700_000_000_000,
    }
    return {
        "kind": "queue",
        "queue_id": queue_id,
        "proposals": [{"proposal": proposal, "source_values": {}}],
    }


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R006")
def test_sink_executes_only_a_released_change_set(
    live_ingest: Any,  # noqa: F811 - pytest fixture
    vendor: _VendorSink,
) -> None:
    async def scenario() -> list[Any]:
        queuer = await _as(QUEUER)
        approver = GeneratedTransport(await scoped_client(["*"]), GRAPH)
        await queuer.log(_op(_queue("q-ok")))
        await queuer.log(_op(_queue("q-pending")))
        await approver.log(
            _op({"kind": "approve", "queue_id": "q-ok", "proposal_id": "wp:q-ok"})
        )
        creations = [{"name": "a"}]
        outs = []
        for change_set_id in ("", "changeset:q-absent", "changeset:q-pending"):
            outs.append(
                await runner.run_released_writeback(
                    queuer,
                    DOMAIN,
                    tenant_id=TENANT,
                    change_set_id=change_set_id,
                    creations=creations,
                )
            )
        assert vendor.live_calls == 0
        released = await runner.run_released_writeback(
            queuer,
            DOMAIN,
            tenant_id=TENANT,
            change_set_id="changeset:q-ok",
            creations=creations,
        )
        outs.append(released)
        outs.append(await release_audit(queuer, "changeset:q-ok", tenant_id=TENANT))
        # The bare queue id names the same release.
        again = await fetch_release(queuer, "q-ok", tenant_id=TENANT)
        outs.append(again)
        return outs

    refused_ids, absent, pending, executed, audit, again = (
        asyncio.run_coroutine_threadsafe(scenario(), live_ingest._loop).result(120)
    )
    assert refused_ids["code"] == RELEASE_ID_MISSING
    assert absent["code"] == RELEASE_NOT_FOUND
    assert pending["code"] == RELEASE_UNAPPROVED
    assert executed["status"] == "completed"
    assert executed["change_set_id"] == "changeset:q-ok"
    assert vendor.live_calls == 1
    assert isinstance(again, ReleasedChangeSet)
    assert again.change_set_id == "changeset:q-ok"
    assert [p["id"] for p in again.proposals] == ["wp:q-ok"]
    assert [row["outcome"] for row in audit].count({"outcome": "released"}) >= 1


class _Down:
    async def log(self, op: Any) -> Any:
        raise DecideUnavailable("engine absent")


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R006")
def test_unreachable_engine_refuses_and_a_release_cannot_be_forged(
    vendor: _VendorSink,
) -> None:
    out = asyncio.run(
        runner.run_released_writeback(
            _Down(),  # type: ignore[arg-type]
            DOMAIN,
            tenant_id=TENANT,
            change_set_id="changeset:q",
            creations=[{"name": "a"}],
        )
    )
    assert out["code"] == RELEASE_UNAVAILABLE and vendor.live_calls == 0
    with pytest.raises(TypeError):
        ReleasedChangeSet("changeset:q", (), ())
    with pytest.raises(ReleaseError):
        asyncio.run(fetch_release(_Down(), "", tenant_id=TENANT))  # type: ignore[arg-type]
    # A local flag no longer approves a high-stakes live write.
    live = runner.run_writeback(DOMAIN, dry_run=False, _approved=True, creations=[{}])
    assert live["status"] in {"refused", "queued"} and vendor.live_calls == 0
