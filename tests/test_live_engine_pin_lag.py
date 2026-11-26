"""The live engine on the pinned release refuses what only the engine's main serves.

Each test starts the real engine through ``tests/live_engine.py`` and drives the SDK
entry point of a row whose engine method is on the engine's main branch but not in the
pinned client. The refusal names the method, nothing is claimed or written, and the
same entry point runs for real once the pin moves (``ENGINE_CAPABILITIES`` then
reports the method present and the guard stops refusing).
"""

from __future__ import annotations

import pytest
from live_engine import _LIVE, _SECRET, _context, live_ingest  # noqa: F401

from scion_sdk.engine_capability import (
    EngineCapabilityUnavailable,
    engine_capability_present,
    require_engine_capability,
)
from scion_sdk.runner.ingest_worker_cli import main as ingest_worker_main


def _engine_client():
    from medulla.client import SyncMedullaClient

    return SyncMedullaClient.connect(
        socket_path=str(_LIVE["socket"]),
        auth_secret=_SECRET,
        verified_context=_context(["connector"], ["*"]),
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.27")
@pytest.mark.spec("SCION-SOURCE-INGEST-R005.30")
def test_live_pinned_engine_cannot_fire_a_job_to_the_worker(live_ingest, capsys):  # noqa: F811
    client = _engine_client()
    try:
        code = ingest_worker_main(
            ["--engine-endpoint", "unix://live"], connect=lambda _endpoint: client
        )
    finally:
        client.close()
    err = capsys.readouterr().err
    assert code == 2
    assert "task_queue_claim" in err and "worker_admit" in err


@pytest.mark.spec("SCION-SOURCE-INGEST-R013")
@pytest.mark.spec("SCION-SOURCE-INGEST-R013.6")
def test_live_pinned_engine_has_no_harness_session_readback(live_ingest):  # noqa: F811
    client = _engine_client()
    try:
        assert not callable(getattr(client, "search_harness_sessions", None))
        assert not callable(getattr(client, "index_harness_session_batch", None))
    finally:
        client.close()


@pytest.mark.spec("SCION-SOURCE-INGEST-R014")
@pytest.mark.spec("SCION-SOURCE-INGEST-R014.6")
def test_live_pinned_engine_refuses_meeting_transcript_revision(live_ingest):  # noqa: F811
    assert not engine_capability_present("MeetingTranscriptRevision")
    with pytest.raises(EngineCapabilityUnavailable, match="MeetingTranscriptRevision"):
        require_engine_capability("MeetingTranscriptRevision")


@pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R001.6")
def test_live_engine_answers_an_unseeded_wallet_key_as_absent(live_ingest):  # noqa: F811
    """The pinned engine serves WalletKeyTrustGet; no trust record is seeded, so the
    resolver answers ``None`` exactly as it would for a foreign record."""
    import asyncio

    from live_engine import scoped_client

    from scion_sdk.writeback.wallet_proof import WalletProofBinding
    from scion_sdk.writeback.wallet_trust import EngineWalletKeyResolver

    binding = WalletProofBinding(
        tenant_id="tenant:sdk-test",
        actor_id="actor",
        connector_id="emerald-exchange",
        operation_id="op",
        account_id="account-absent",
        wallet_id="wallet-absent",
        network_id="network",
        key_id="key-absent",
        plan_digest="a" * 64,
        change_set_digest="b" * 64,
        change_set_id="changeset:1",
        idempotency_key="idem",
    )

    async def resolve():
        client = await scoped_client(["finance:records"])
        try:
            return await EngineWalletKeyResolver(client).resolve(binding)
        finally:
            await client.close()

    assert (
        asyncio.run_coroutine_threadsafe(resolve(), live_ingest._loop).result(30)
        is None
    )
