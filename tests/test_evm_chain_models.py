"""EVM models retain exact values and refuse unimplemented extraction."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scion_sdk.adapters.event_envelope import FeedKind
from scion_sdk.adapters.event_feed import EventFeedSourceAdapter
from scion_sdk.adapters.evm_chain import EvmLogDescriptor, EvmTrade
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.ports.errors import SourceContractError

ADDRESS = "0x" + "a" * 40
HASH = "0x" + "b" * 64


def descriptor(**changes):
    values = {
        "chain_id": 1,
        "contract": ADDRESS,
        "event_signature": HASH,
        "decoder_revision": "v1",
        "first_block": 0,
        "last_block": 100,
    }
    return EvmLogDescriptor.model_validate({**values, **changes})


def trade(**changes):
    values = {
        "chain_id": 1,
        "contract": ADDRESS,
        "event_signature": HASH,
        "decoder_revision": "v1",
        "transaction_hash": HASH,
        "block_hash": HASH,
        "block_number": 2,
        "log_index": 1,
        "asset_in": ADDRESS,
        "asset_out": ADDRESS,
        "amount_in": 2**200 + 1,
        "amount_out": 42,
        "scale_in": 18,
        "scale_out": 6,
        "side": "buy",
    }
    return EvmTrade.model_validate({**values, **changes})


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.13")
@pytest.mark.spec("SCION-SOURCE-INGEST-R005.47")
def test_evm_models_validate_identity_and_exact_numbers() -> None:
    value = trade()
    assert value.amount_in == 2**200 + 1
    assert trade(amount_in=0).amount_in == 0
    assert descriptor().last_block == 100
    for changes in (
        {"chain_id": 0},
        {"contract": "bad"},
        {"contract": ADDRESS + "\n"},
        {"event_signature": "bad"},
        {"event_signature": HASH + "\n"},
        {"decoder_revision": ""},
        {"last_block": -1},
        {"first_block": 101},
    ):
        with pytest.raises(ValidationError):
            descriptor(**changes)
    for changes in (
        {"amount_in": 1.0},
        {"amount_in": True},
        {"amount_in": 2**256},
        {"scale_in": 256},
        {"transaction_hash": "bad"},
        {"log_index": -1},
        {"side": "unknown"},
    ):
        with pytest.raises(ValidationError):
            trade(**changes)
    with pytest.raises(ValidationError):
        value.amount_in = 1


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.13")
@pytest.mark.spec("SCION-SOURCE-INGEST-R005.47")
@pytest.mark.asyncio
async def test_evm_descriptor_refuses_undecoded_extraction() -> None:
    preset = ToolPreset(name="chain", server="server", tool="logs", id_field="id")
    adapter = EventFeedSourceAdapter(
        preset,
        feed_kind=FeedKind.RUM,
        connector="example",
        tool_schema_sha256="pin",
        mapping_reference="map",
        chain_descriptor=descriptor(),
    )
    assert adapter.chain_descriptor == descriptor()
    with pytest.raises(
        SourceContractError, match="chain-log decoding is not available"
    ):
        await adapter.discover(SimpleNamespace())
    with pytest.raises(
        SourceContractError, match="chain-log decoding is not available"
    ):
        await adapter.extract(SimpleNamespace(), None)


def _word(value: int) -> str:
    return f"{value:064x}"


LAYOUT = {
    "asset_in": ("topic", 1),
    "asset_out": ("topic", 2),
    "amount_in": ("data", 0),
    "amount_out": ("data", 1),
    "scale_in": 18,
    "scale_out": 6,
    "side_word": 2,
}


def _log(index: int = 1, block: int = 5, *, side: int = 0, **changes):
    log = {
        "address": ADDRESS,
        "topics": [
            HASH,
            "0x" + _word(int(ADDRESS, 16)),
            "0x" + _word(int(HASH[:42], 16)),
        ],
        "data": "0x" + _word(2**200 + 1) + _word(42) + _word(side),
        "blockNumber": hex(block),
        "blockHash": "0x" + f"{block:064x}",
        "transactionHash": "0x" + f"{index:064x}",
        "logIndex": hex(index),
    }
    return {**log, **changes}


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.14")
async def test_decode_known_logs_and_identity_is_stable_under_duplicates() -> None:
    from scion_sdk.adapters.evm_chain import decode_trade_log, read_trade_logs

    desc = descriptor(layout=LAYOUT, last_block=8, first_block=0)
    one = decode_trade_log(desc, _log())
    assert one.amount_in == 2**200 + 1 and one.amount_out == 42 and one.side == "buy"
    assert decode_trade_log(desc, _log(side=1)).side == "sell"
    calls = []

    async def rpc(method, params):
        calls.append((method, params))
        return [_log(3, 6), _log(1, 5), _log(1, 5)]

    trades, rejected = await read_trade_logs(rpc, desc)
    assert [t.log_index for t in trades] == [1, 3] and rejected == []
    assert calls[0][1][0]["fromBlock"] == "0x0" and len(calls) == 1
    from scion_sdk.adapters.event_errors import ChainLogRefused

    with pytest.raises(ChainLogRefused):
        await read_trade_logs(rpc, descriptor(layout=LAYOUT, last_block=50))


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.16")
async def test_malformed_logs_rpc_errors_and_changed_hashes_are_refused() -> None:
    from scion_sdk.adapters.event_errors import ChainLogRefused
    from scion_sdk.adapters.evm_chain import decode_trade_log, read_trade_logs

    desc = descriptor(layout=LAYOUT, last_block=8)
    for changes, reason in (
        ({"address": "0x" + "c" * 40}, "CONTRACT_MISMATCH"),
        ({"topics": ["0x" + "c" * 64]}, "SIGNATURE_UNKNOWN"),
        ({"chainId": "0x2"}, "CHAIN_MISMATCH"),
        ({"data": "0x12"}, "LOG_UNDECODABLE"),
        ({"blockNumber": "nope"}, "LOG_UNDECODABLE"),
    ):
        with pytest.raises(ChainLogRefused) as caught:
            decode_trade_log(desc, _log(**changes))
        assert caught.value.reason == reason
    with pytest.raises(ValueError):
        ChainLogRefused("NOPE")

    async def malformed(method, params):
        return "not-a-list"

    with pytest.raises(ChainLogRefused) as caught:
        await read_trade_logs(malformed, desc)
    assert caught.value.reason == "RPC_REPLY_MALFORMED"

    async def mixed(method, params):
        return [
            _log(1, 5),
            _log(2, 5, blockHash="0x" + "9" * 64),
            _log(3, 6, data="0x1"),
        ]

    with pytest.raises(ChainLogRefused) as caught:
        await read_trade_logs(mixed, desc)
    assert caught.value.reason == "BLOCK_HASH_INCONSISTENT"

    async def partly_bad(method, params):
        return [_log(1, 5), _log(2, 6, data="0x1")]

    trades, rejected = await read_trade_logs(partly_bad, desc)
    assert len(trades) == 1 and rejected[0].reason == "LOG_UNDECODABLE"
    assert rejected[0].identity == "0x" + f"{2:064x}"


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.17")
def test_trade_evidence_binds_decoder_input_and_redacts() -> None:
    import hashlib
    import json

    from scion_sdk.adapters.evm_chain import decode_trade_log

    desc = descriptor(layout=LAYOUT, last_block=8)
    log = _log()
    one = decode_trade_log(desc, log)
    raw = json.dumps(log, sort_keys=True, separators=(",", ":"), default=str)
    assert one.raw_log_sha256 == hashlib.sha256(raw.encode()).hexdigest()
    assert (one.chain_id, one.contract, one.block_number, one.decoder_revision) == (
        1,
        ADDRESS,
        5,
        "v1",
    )
    assert (
        one.block_hash == log["blockHash"]
        and one.transaction_hash == log["transactionHash"]
    )
    assert decode_trade_log(desc, log) == one
    from scion_sdk.adapters.evm_chain import EvmFieldLayout

    assert desc.layout == EvmFieldLayout.model_validate(LAYOUT)
    assert "data" not in one.model_dump()
