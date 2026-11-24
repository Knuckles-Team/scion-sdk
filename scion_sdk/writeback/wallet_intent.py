"""Closed wallet-operation intent, confirmation and execution envelope.

The intent is the business operation alone; the plan digest is always
derived from it, and the confirmation and the proof travel beside it in
the envelope. A verification result is a fact and never an authorization
grant.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    field_validator,
)

from scion_sdk.writeback._contract_json import _canonical_bytes
from scion_sdk.writeback.wallet_proof import WalletProofRequest

_MAX_JSON_INTEGER = 2**53 - 1
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PARAMS_BYTES = 16384

__all__ = [
    "WalletBusinessIntent",
    "WalletConfirmation",
    "WalletExecutionEnvelope",
    "wallet_plan_digest",
]


class WalletBusinessIntent(BaseModel):
    """The wallet operation itself; it never contains the proof or the
    plan digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    wallet_id: str = Field(min_length=1)
    network_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def params_are_a_bounded_object(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if len(_canonical_bytes(value)) > _MAX_PARAMS_BYTES:
            raise ValueError("wallet params exceed the canonical size limit")
        return value


class WalletConfirmation(BaseModel):
    """One confirmation of one exact plan, bound by the plan digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    confirmed_by: str = Field(min_length=1)
    confirmed_at_ms: StrictInt = Field(ge=0, le=_MAX_JSON_INTEGER)
    plan_digest: str

    @field_validator("plan_digest")
    @classmethod
    def plan_digest_is_sha256(cls, value: str) -> str:
        if not _DIGEST.fullmatch(value):
            raise ValueError("invalid digest")
        return value


class WalletExecutionEnvelope(BaseModel):
    """Intent plus confirmation, and the proof when one has been made."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    intent: WalletBusinessIntent
    confirmation: WalletConfirmation
    proof: WalletProofRequest | None = None


def wallet_plan_digest(intent: WalletBusinessIntent) -> str:
    """SHA-256 of the RFC 8785 canonical JSON of ``{"intent": intent}``.

    Returns 64 lowercase hex characters.
    """
    return hashlib.sha256(
        _canonical_bytes({"intent": intent.model_dump(mode="json")})
    ).hexdigest()
