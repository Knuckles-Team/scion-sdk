"""Sink grant and canonical change-set construction for governed write-back."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from ._governed_common import PATCH_FIELD, _digest, _write_back
from .core import PROVENANCE_TAG, _require_identifier

if TYPE_CHECKING:
    from epistemic_graph.generated.write_back import (
        SourceChangeSet,
        WriteBackAuthorizationDecision,
        WriteBackAuthorizationMode,
    )

__all__ = ["SinkGrant", "build_change_set"]

#: SDK connector id prefix for AU write-back sinks.
_CONNECTOR_PREFIX = "agent-utilities.writeback"
#: source version used when the caller supplies no version signal.
_UNVERSIONED = "unversioned"
_TENANT = "agent-utilities"
_ACTOR = "agent-utilities:graph_writeback"
_TTL_MS = 300_000
_SCHEMA_VERSION = 1


def _json_ops(ops: dict[str, Any]) -> dict[str, Any]:
    """Project ``ops`` to canonical JSON, dropping private keys and clients."""
    public = {
        key: value
        for key, value in ops.items()
        if not key.startswith("_") and key != "client"
    }
    return cast(
        "dict[str, Any]", json.loads(json.dumps(public, sort_keys=True, default=str))
    )


@dataclass(frozen=True)
class SinkGrant:
    """The fail-closed write grant that ``run_writeback`` already resolved."""

    target: str
    enable_flag: str
    write_enabled: bool
    risk_tier: str = "standard"
    approved: bool = False

    def __post_init__(self) -> None:
        """Reject blank identity fields before constructing a change set.

        The generated graph contract validates the shape of the resulting
        change set, but its string identifiers intentionally have no minimum
        length.  Validate the two SDK-owned identifiers at the grant boundary
        so ``:batch:...`` or ``... .`` connector identities cannot be emitted.
        """
        for field_name in ("target", "enable_flag"):
            _require_identifier(getattr(self, field_name), field_name)

    def mode(self) -> WriteBackAuthorizationMode:
        """Approved proposals replay under approval; others under standing policy."""
        if self.approved:
            return cast(
                "WriteBackAuthorizationMode",
                _write_back().WriteBackAuthorizationMode.PROPOSAL_APPROVAL,
            )
        return cast(
            "WriteBackAuthorizationMode",
            _write_back().WriteBackAuthorizationMode.STANDING_POLICY,
        )

    def policy_digest(self) -> str:
        """Digest the policy inputs the grant was decided from."""
        return _digest(
            {
                "enable_flag": self.enable_flag,
                "risk_tier": self.risk_tier,
                "write_enabled": self.write_enabled,
                "approved": self.approved,
            }
        )

    def decision(self, input_digest: str) -> WriteBackAuthorizationDecision:
        """Return the exact authorization decision for one input digest."""
        output_digest = _digest({"target": self.target, "input": input_digest})
        fields = {
            "mode": self.mode().value,
            "authorization_ref": self.enable_flag,
            "authorized": self.write_enabled,
            "input_digest": input_digest,
            "output_digest": output_digest,
            "policy_digest": self.policy_digest(),
        }
        return cast(
            "WriteBackAuthorizationDecision",
            _write_back().WriteBackAuthorizationDecision(
                mode=self.mode(),
                authorization_ref=self.enable_flag,
                authorized=self.write_enabled,
                decision_digest=_digest(fields),
                input_digest=input_digest,
                output_digest=output_digest,
            ),
        )


def _base_version(ops: dict[str, Any]) -> str:
    return str(ops.get("expected_source_version") or _UNVERSIONED)


def build_change_set(
    grant: SinkGrant, ops: dict[str, Any], *, now_ms: int | None = None
) -> SourceChangeSet:
    """Build the canonical SDK change set for one sink invocation."""
    patch = {PATCH_FIELD: _json_ops(ops)}
    input_digest = _digest(patch)
    change_set_id = uuid.uuid4().hex
    issued_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    draft = _write_back().SourceChangeSet(
        actor=_ACTOR,
        authorization=grant.decision(input_digest),
        base_source_version=_base_version(ops),
        change_set_digest="0" * 64,
        change_set_id=change_set_id,
        connector_id=f"{_CONNECTOR_PREFIX}.{grant.target}",
        desired_patch=patch,
        entity_id=f"{grant.target}:batch:{input_digest[:16]}",
        expires_at_ms=issued_ms + _TTL_MS,
        field_provenance={PATCH_FIELD: PROVENANCE_TAG},
        field_scope=[PATCH_FIELD],
        idempotency_key=f"{grant.target}:{change_set_id}",
        policy_digest=grant.policy_digest(),
        purpose="graph_writeback",
        reconciliation_procedure="operator-reconciles: sink has no read-back",
        required_capability=f"writeback:{grant.target}",
        schema_version=_SCHEMA_VERSION,
        source_instance_id=grant.target,
        source_of_truth_rule="system-of-record-wins",
        tenant_id=_TENANT,
    )
    return cast(
        "SourceChangeSet",
        draft.model_copy(update={"change_set_digest": draft.canonical_digest()}),
    )
