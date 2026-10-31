"""Shared write-back runner: gate, governed dispatch and uniform manifests."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._env import setting
from .core import (
    WritebackContext,
    WritebackSink,
    get_sink,
    list_sinks,
)

if TYPE_CHECKING:
    from .governed import GovernedOutcome, SinkGrant

logger = logging.getLogger(__name__)

__all__ = ["run_writeback"]


@dataclass(frozen=True)
class _SinkCall:
    """One resolved sink invocation shared by the governed dispatch helpers."""

    target: str
    sink: WritebackSink
    ctx: WritebackContext
    ops: dict[str, Any]
    grant: SinkGrant


def _stamp_proposals_as_of(
    proposals: list[dict[str, Any]], as_of: str | None
) -> list[dict[str, Any]]:
    """Attach ``as_of`` to each proposal — the write-path audit-trail twin of the
    read path's long-standing ``as_of`` support (CONCEPT:AU-KG.temporal.bi-temporal-memory-layers, X5).

    A falsy ``as_of`` (the default when a caller doesn't supply one) returns
    ``proposals`` unchanged, so every pre-existing caller sees byte-identical
    output. An already-stamped proposal (a sink that sets its own ``as_of``) is
    never overwritten. Never mutates the input list/dicts in place.
    """
    if not as_of:
        return proposals
    return [
        {**p, "as_of": as_of} if isinstance(p, dict) and "as_of" not in p else p
        for p in proposals
    ]


def run_writeback(
    target: str,
    *,
    backend: Any = None,
    engine: Any = None,
    dry_run: bool = True,
    as_of: str | None = None,
    **ops: Any,
) -> dict[str, Any]:
    """Single fail-closed, dry-run-first write-back entrypoint (MCP + REST core).

    Resolves the ``target`` sink, enforces its ``enable_flag`` for live writes,
    and returns a uniform manifest. ``ops`` carries target-specific payloads
    (inferences / enrichments / creations / retirements / process_ids / …).

    ``as_of`` (ISO-8601, CONCEPT:AU-KG.temporal.bi-temporal-memory-layers) is the bitemporal
    valid-time this pass asserts its writes are valid as of — threaded onto the
    :class:`WritebackContext` a sink receives so it can stamp
    ``storage_time``/``event_time``/``valid_from``/``valid_to`` (via
    :meth:`WritebackContext.stamp_valid_time`, and automatically for any
    :meth:`WritebackContext.stamp_external_id` call) on every mirrored record.
    Defaults to ``None`` ("now" at stamp time) so a caller that doesn't pass it
    behaves byte-for-byte as before this parameter existed.

    **X5 write-path closure:** when ``as_of`` is supplied, it is ALSO stamped
    onto every returned proposal (:func:`_stamp_proposals_as_of`) — including
    a high-stakes sink's queued :class:`~.approval.ProposalQueue` entry — so
    the outbound audit trail records the KG-state instant a backfeed derived
    from, not just the mirrored record's own bitemporal validity. A sink may
    additionally embed ``ctx.as_of`` directly into the LIVE payload it sends
    (e.g. the ServiceNow/Egeria sinks); this function only guarantees the
    audit-trail (proposal) side, which applies uniformly to every sink with no
    per-sink change required.
    """
    sink = get_sink(target)
    if sink is None:
        return {
            "status": "error",
            "error": f"unknown write-back target {target!r}",
            "available": list_sinks(),
        }
    write_enabled = bool(setting(sink.enable_flag, False, cast=bool))
    if not dry_run and not write_enabled:
        return {
            "status": "refused",
            "target": target,
            "reason": f"{sink.enable_flag} not set; refusing live write to the system-of-record",
            "hint": "run with dry_run=true to preview the proposed writes",
        }
    ctx = WritebackContext(backend=backend, engine=engine, as_of=as_of)

    # CA-22/P11 (DEC-CA-07): every write path calls evaluate_backfeed_preflight
    # before committing, ahead of the high-stakes branch, so a preflight
    # rejection blocks even before a ProposalQueue entry is created.
    blocked = _backfeed_preflight(target, sink, ops)
    if blocked is not None:
        return blocked

    # Per the AU boundary deconstruction spec, every sink call below runs through the SDK governed
    # write-back contract (canonical change set, audit reservation, ledger).
    from .governed import SinkGrant

    grant = SinkGrant(
        target=sink.domain,
        enable_flag=sink.enable_flag,
        write_enabled=write_enabled,
        risk_tier=getattr(sink, "risk_tier", "standard"),
        approved=bool(ops.get("_approved")),
    )
    call = _SinkCall(target, sink, ctx, ops, grant)
    # High-stakes sinks NEVER auto-execute: a live request (enabled, not dry-run,
    # not carrying an approval token) is previewed and queued for approval instead.
    if grant.risk_tier == "high_stakes" and not dry_run and not grant.approved:
        return _queue_high_stakes(call)
    return _governed_manifest(call, dry_run=dry_run)


def _backfeed_preflight(
    target: str, sink: WritebackSink, ops: dict[str, Any]
) -> dict[str, Any] | None:
    """Return a refusal manifest when a caller-supplied preflight blocks the write.

    ``ops["_preflight"]`` is an optional callable ``(target, sink, ops)`` returning
    ``None`` to proceed or a ``{"status": "refused"|"queued", ...}`` manifest. The
    source-conflict decision itself is made by the graph engine, not here.
    """
    hook = ops.get("_preflight")
    if not callable(hook):
        return None
    blocked = hook(target, sink, ops)
    return blocked if isinstance(blocked, dict) else None


def _governed_call(
    call: _SinkCall, *, dry_run: bool
) -> GovernedOutcome | dict[str, Any]:
    """Run one sink call through the SDK; return its outcome or a failure manifest."""
    from scion_sdk.writeback.errors import WriteBackError

    from .governed import execute_governed

    target = call.target
    try:
        return execute_governed(
            call.sink,
            call.ctx,
            call.ops,
            grant=call.grant,
            dry_run=dry_run,
            _capture_failure=True,
        )
    except WriteBackError as e:
        logger.debug("governed write-back refused for %s", target, exc_info=True)
        return {
            "status": "refused",
            "target": target,
            "reason": f"governed write-back refused: {e}",
        }
    except Exception as e:  # never let one sink crash the surface
        logger.debug("writeback sink %s failed", target, exc_info=True)
        return {"status": "error", "target": target, "error": str(e)}


def _failure_message(error: BaseException) -> str:
    """Return a stable manifest message even for empty timeout/cancel errors."""
    return str(error) or type(error).__name__


def _queue_high_stakes(call: _SinkCall) -> dict[str, Any]:
    """Preview a high-stakes write through the SDK and queue it for approval."""
    target, ctx, ops, grant = call.target, call.ctx, call.ops, call.grant
    outcome = _governed_call(call, dry_run=True)
    if isinstance(outcome, dict):
        return outcome
    preview = outcome.result
    preview.proposals = _stamp_proposals_as_of(preview.proposals, ctx.as_of)
    enqueue = ops.get("_enqueue_proposal")
    if not callable(enqueue):
        return {
            "status": "refused",
            "target": target,
            "reason": "high-stakes write needs an approval queue (_enqueue_proposal)",
        }
    pid = enqueue(target, ops, preview.proposals)
    out = preview.as_dict()
    out.update(
        {
            "status": "queued",
            "target": target,
            "risk_tier": grant.risk_tier,
            "proposal_id": pid,
            "as_of": ctx.as_of,
            "governed_writeback": outcome.evidence(),
            "hint": "high-stakes write queued; approve through the engine decision queue",
        }
    )
    return out


def _governed_manifest(call: _SinkCall, *, dry_run: bool) -> dict[str, Any]:
    """Run the sink through the SDK and return the uniform write-back manifest."""
    target, ctx, grant = call.target, call.ctx, call.grant
    outcome = _governed_call(call, dry_run=dry_run)
    if isinstance(outcome, dict):
        return outcome
    result = outcome.result
    result.proposals = _stamp_proposals_as_of(result.proposals, ctx.as_of)
    out = result.as_dict()
    if outcome.failure is not None:
        out.update(
            {
                "status": "error",
                "target": target,
                "dry_run": dry_run,
                "write_enabled": grant.write_enabled,
                "risk_tier": grant.risk_tier,
                "as_of": ctx.as_of,
                "error": _failure_message(outcome.failure),
                "governed_writeback": outcome.evidence(),
            }
        )
        return out
    out["status"] = "completed"
    out["target"] = target
    out["dry_run"] = dry_run
    out["write_enabled"] = grant.write_enabled
    out["risk_tier"] = grant.risk_tier
    out["as_of"] = ctx.as_of
    out["governed_writeback"] = outcome.evidence()
    return out
