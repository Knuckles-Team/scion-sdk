"""Typed EVM log and exact trade descriptors, without a live decoder."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from scion_sdk.adapters.event_errors import ChainLogRefused

__all__ = [
    "EvmFieldLayout",
    "EvmLogDescriptor",
    "EvmTrade",
    "decode_trade_log",
    "read_trade_logs",
]

_UINT256_MAX = 2**256 - 1
_ADDRESS = r"^0x[0-9a-fA-F]{40}\z"
_HASH = r"^0x[0-9a-fA-F]{64}\z"


class EvmFieldLayout(BaseModel):
    """Where each trade field sits in the log: a topic index or a data word index.

    Addresses and amounts are 32-byte words. ``side_word`` names the data word
    whose value 0 means ``buy`` and anything else ``sell``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_in: tuple[Literal["topic", "data"], StrictInt]
    asset_out: tuple[Literal["topic", "data"], StrictInt]
    amount_in: tuple[Literal["topic", "data"], StrictInt]
    amount_out: tuple[Literal["topic", "data"], StrictInt]
    scale_in: StrictInt = Field(ge=0, le=255)
    scale_out: StrictInt = Field(ge=0, le=255)
    side_word: StrictInt = Field(ge=0)


class EvmLogDescriptor(BaseModel):
    """The explicit chain, contract, event and bounded block range to decode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_id: StrictInt = Field(gt=0, le=_UINT256_MAX)
    contract: str = Field(pattern=_ADDRESS)
    event_signature: str = Field(pattern=_HASH)
    decoder_revision: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\z"
    )
    first_block: StrictInt = Field(ge=0, le=_UINT256_MAX)
    last_block: StrictInt = Field(ge=0, le=_UINT256_MAX)
    layout: EvmFieldLayout | None = None

    @model_validator(mode="after")
    def _ordered_blocks(self) -> Self:
        if self.last_block < self.first_block:
            raise ValueError("last_block precedes first_block")
        return self


class EvmTrade(BaseModel):
    """Trade values and immutable event provenance without float conversion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chain_id: StrictInt = Field(gt=0, le=_UINT256_MAX)
    contract: str = Field(pattern=_ADDRESS)
    event_signature: str = Field(pattern=_HASH)
    decoder_revision: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\z"
    )
    transaction_hash: str = Field(pattern=_HASH)
    block_hash: str = Field(pattern=_HASH)
    block_number: StrictInt = Field(ge=0, le=_UINT256_MAX)
    log_index: StrictInt = Field(ge=0, le=_UINT256_MAX)
    asset_in: str = Field(pattern=_ADDRESS)
    asset_out: str = Field(pattern=_ADDRESS)
    amount_in: StrictInt = Field(ge=0, le=_UINT256_MAX)
    amount_out: StrictInt = Field(ge=0, le=_UINT256_MAX)
    scale_in: StrictInt = Field(ge=0, le=255)
    scale_out: StrictInt = Field(ge=0, le=255)
    side: Literal["buy", "sell"]
    raw_log_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}\z")


_MAX_BLOCK_RANGE = 10
_MAX_EVENTS = 100


def _hex(value: object, length: int) -> str:
    if not isinstance(value, str) or len(value) != 2 + length * 2:
        raise ChainLogRefused("LOG_UNDECODABLE")
    try:
        int(value, 16)
    except ValueError:
        raise ChainLogRefused("LOG_UNDECODABLE") from None
    return value.lower()


def _words(log: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    topics = log.get("topics")
    data = _hex_data(log.get("data"))
    if not isinstance(topics, list) or not topics:
        raise ChainLogRefused("LOG_UNDECODABLE")
    return [_hex(t, 32) for t in topics], data


def _hex_data(value: object) -> list[str]:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ChainLogRefused("LOG_UNDECODABLE")
    body = value[2:].lower()
    if len(body) % 64 or any(c not in "0123456789abcdef" for c in body):
        raise ChainLogRefused("LOG_UNDECODABLE")
    return ["0x" + body[i : i + 64] for i in range(0, len(body), 64)]


def _word(source: tuple[str, int], topics: list[str], data: list[str]) -> str:
    pool = topics if source[0] == "topic" else data
    if not 0 <= source[1] < len(pool):
        raise ChainLogRefused("LOG_UNDECODABLE")
    return pool[source[1]]


def _address(word: str) -> str:
    if int(word, 16) >> 160:
        raise ChainLogRefused("LOG_UNDECODABLE")
    return "0x" + word[-40:]


def decode_trade_log(descriptor: EvmLogDescriptor, log: Mapping[str, Any]) -> EvmTrade:
    """Decode one log by the declared layout; amounts stay integers."""
    layout = descriptor.layout
    if layout is None:
        raise ChainLogRefused("LOG_UNDECODABLE")
    identity = str(log.get("transactionHash", ""))[:66]
    try:
        return _decode(descriptor, layout, log)
    except ChainLogRefused as refused:
        raise ChainLogRefused(refused.reason, identity) from None


def _decode(
    descriptor: EvmLogDescriptor, layout: EvmFieldLayout, log: Mapping[str, Any]
) -> EvmTrade:
    chain = log.get("chainId")
    if chain is not None and int(str(chain), 16) != descriptor.chain_id:
        raise ChainLogRefused("CHAIN_MISMATCH")
    if _hex(log.get("address"), 20) != descriptor.contract.lower():
        raise ChainLogRefused("CONTRACT_MISMATCH")
    topics, data = _words(log)
    if topics[0] != descriptor.event_signature.lower():
        raise ChainLogRefused("SIGNATURE_UNKNOWN")
    number = _quantity(log.get("blockNumber"))
    if not descriptor.first_block <= number <= descriptor.last_block:
        raise ChainLogRefused("LOG_UNDECODABLE")
    side_words = data[layout.side_word : layout.side_word + 1]
    if not side_words:
        raise ChainLogRefused("LOG_UNDECODABLE")
    raw = json.dumps(log, sort_keys=True, separators=(",", ":"), default=str)
    return EvmTrade(
        chain_id=descriptor.chain_id,
        contract=descriptor.contract,
        event_signature=descriptor.event_signature,
        decoder_revision=descriptor.decoder_revision,
        transaction_hash=_hex(log.get("transactionHash"), 32),
        block_hash=_hex(log.get("blockHash"), 32),
        block_number=number,
        log_index=_quantity(log.get("logIndex")),
        asset_in=_address(_word(layout.asset_in, topics, data)),
        asset_out=_address(_word(layout.asset_out, topics, data)),
        amount_in=int(_word(layout.amount_in, topics, data), 16),
        amount_out=int(_word(layout.amount_out, topics, data), 16),
        scale_in=layout.scale_in,
        scale_out=layout.scale_out,
        side="buy" if int(side_words[0], 16) == 0 else "sell",
        raw_log_sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )


def _quantity(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ChainLogRefused("LOG_UNDECODABLE")
    try:
        return int(value, 16)
    except ValueError:
        raise ChainLogRefused("LOG_UNDECODABLE") from None


async def read_trade_logs(
    rpc: Callable[[str, list[Any]], Awaitable[object]],
    descriptor: EvmLogDescriptor,
    *,
    max_block_range: int = _MAX_BLOCK_RANGE,
    max_events: int = _MAX_EVENTS,
) -> tuple[list[EvmTrade], list[ChainLogRefused]]:
    """Read one bounded block range through ``rpc`` and decode it.

    Returns the trades in cursor order (block, transaction position, log index)
    and the per-log rejections. A malformed RPC reply or a block hash that
    changes within the page raises, so the page never advances.
    """
    if descriptor.last_block - descriptor.first_block + 1 > max_block_range:
        raise ChainLogRefused("RPC_REPLY_MALFORMED")
    reply = await rpc(
        "eth_getLogs",
        [
            {
                "fromBlock": hex(descriptor.first_block),
                "toBlock": hex(descriptor.last_block),
                "address": descriptor.contract,
                "topics": [descriptor.event_signature],
            }
        ],
    )
    if not isinstance(reply, Sequence) or isinstance(reply, str | bytes):
        raise ChainLogRefused("RPC_REPLY_MALFORMED")
    if len(reply) > max_events:
        raise ChainLogRefused("RPC_REPLY_MALFORMED")
    trades: list[EvmTrade] = []
    rejected: list[ChainLogRefused] = []
    hashes: dict[int, str] = {}
    for log in reply:
        if not isinstance(log, Mapping):
            raise ChainLogRefused("RPC_REPLY_MALFORMED")
        try:
            trade = decode_trade_log(descriptor, log)
        except ChainLogRefused as refused:
            rejected.append(refused)
            continue
        if hashes.setdefault(trade.block_number, trade.block_hash) != trade.block_hash:
            raise ChainLogRefused("BLOCK_HASH_INCONSISTENT", trade.transaction_hash)
        trades.append(trade)
    identities = {(t.transaction_hash, t.log_index): t for t in trades}
    ordered = sorted(identities.values(), key=lambda t: (t.block_number, t.log_index))
    return ordered, rejected
