"""Unified write-back core: result, resolver, gate, sink registry (CONCEPT:MEDULLA-KG.storage.nonblocking-checkpoint/2.9).

Bitemporal ``as_of`` on writeback (CONCEPT:MERISTEM-KG.temporal.bi-temporal-memory-layers): read
paths (``engine_query.py``, ``hybrid_retriever.py``, ``context_compiler.py``) have long
accepted an ``as_of`` instant and filtered rows via
:func:`~agent_utilities.knowledge_graph.core.bitemporal.filter_as_of`; nothing on the
write side stamped the valid-time a KG-derived fact was mirrored under. ``run_writeback``'s
optional ``as_of`` (default ``None`` → wall-clock "now", so every existing caller is
unaffected byte-for-byte) is threaded onto :class:`WritebackContext` and applied by
:meth:`WritebackContext.stamp_valid_time` — call it from a sink alongside
:meth:`WritebackContext.stamp_external_id` to stamp the SAME
``storage_time``/``event_time``/``valid_from``/``valid_to`` quadruple the read path already
understands onto a mirrored record, so a later ``as_of`` query can answer "what did we mirror
as valid at time T", not only "what is the current value."

**X5 write-path closure (W3.4):** the above stamps the MIRRORED record's own KG-side
validity; it says nothing about which KG-state instant the OUTBOUND write to the
system-of-record was derived from. :func:`run_writeback` now also stamps every returned
proposal with ``as_of`` (:func:`_stamp_proposals_as_of` — audit-trail coverage for every
sink with no per-sink change), and the ServiceNow/Egeria sinks additionally embed
``ctx.as_of`` into the LIVE payload itself (the ServiceNow ``work_notes`` text; Egeria's
``additional_properties``) so the receiving system's own record carries it too.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol, runtime_checkable

from ._env import stamp_bitemporal
from ._external_ids import _upsert_stamp, resolve_external_id

logger = logging.getLogger(__name__)

#: provenance tag stamped on agent-written (inferred) upstream relations/records.
PROVENANCE_TAG = "agent-utilities:inferred"


def _require_identifier(value: object, field_name: str) -> str:
    """Return a non-blank SDK identifier or reject it at construction."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty identifier")
    return value


@dataclass
class WritebackResult:
    """One result type for every write-back target (counts + dry-run proposals)."""

    target: str = ""
    created: int = 0
    enriched: int = 0
    relations_written: int = 0
    retired: int = 0
    skipped: int = 0
    errors: int = 0
    proposals: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_identifier(self.target, "target")

    def merge(self, other: WritebackResult) -> WritebackResult:
        self.created += other.created
        self.enriched += other.enriched
        self.relations_written += other.relations_written
        self.retired += other.retired
        self.skipped += other.skipped
        self.errors += other.errors
        self.proposals.extend(other.proposals)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "enriched": self.enriched,
            "relations_written": self.relations_written,
            "retired": self.retired,
            "skipped": self.skipped,
            "errors": self.errors,
            "proposals": self.proposals,
        }


@dataclass
class WritebackContext:
    """What a sink needs to do its work — the live backend + engine."""

    backend: Any = None
    engine: Any = None
    #: bitemporal valid-time (ISO-8601) this writeback pass asserts its writes
    #: are valid as of — CONCEPT:MERISTEM-KG.temporal.bi-temporal-memory-layers.
    #: ``None`` (the default) means "now": :meth:`stamp_valid_time` then
    #: stamps wall-clock storage/event/valid_from, matching every caller's
    #: pre-existing behavior byte-for-byte.
    as_of: str | None = None

    def resolver(self, domain: str) -> Callable[[str], str | None]:
        return resolve_external_id(self.backend, domain)

    def stamp_valid_time(self, props: dict[str, Any]) -> dict[str, Any]:
        """Inject this context's bitemporal validity onto ``props`` (in place, returned).

        Thin wrapper over :func:`~agent_utilities.knowledge_graph.core.bitemporal.
        stamp_bitemporal` using ``self.as_of`` as the asserted ``event_time`` —
        ``as_of=None`` (default) resolves to wall-clock "now", identical to every
        writeback call made before this context carried an ``as_of``.
        """
        return stamp_bitemporal(props, event_time=self.as_of)

    def stamp_external_id(
        self,
        node_id: str | None,
        target: str,
        external_id: str | None,
        *,
        node_type: str = "",
    ) -> bool:
        """Round-trip the SoR's returned CI id back onto the source KG node.

        After a sink creates a CI/asset/record it calls this with the returned id
        so the node carries three stamps: ``<target>_ci_id`` (the per-sink identity
        used to dedupe re-runs — idempotency across ALL sinks), and the shared
        ``domain`` / ``externalToolId`` federation key the resolver reads. Writing
        the stamp turns the next :func:`~.inventory.collect_inventory_creations`
        pass into a skip/update instead of a duplicate create. Also stamps this
        context's bitemporal validity (:meth:`stamp_valid_time`) onto the SAME
        props, so the mirrored record carries a ``valid_from``/``valid_to`` an
        ``as_of`` read can filter on (CONCEPT:MERISTEM-KG.temporal.bi-temporal-memory-layers).

        Best-effort (fail-closed): a stamp failure never breaks the sink write —
        it only means the node may be re-proposed next pass.
        """
        if not (node_id and external_id) or self.engine is None:
            return False
        eid = str(external_id)
        props = self.stamp_valid_time(
            {
                f"{(target or '').lower().strip()}_ci_id": eid,
                "externalToolId": eid,
                "domain": (target or "").lower().strip(),
            }
        )
        add_node = getattr(self.engine, "add_node", None)
        if add_node is None:
            return False
        return _upsert_stamp(
            add_node,
            node_id,
            label=node_type or "ConfigurationItem",
            props=props,
        )


class InvocationParts(NamedTuple):
    """Sink-local values of one invocation; unpacks as a 5-tuple."""

    ctx: WritebackContext
    ops: dict[str, Any]
    client: Any | None
    result: WritebackResult
    dry_run: bool


@dataclass(frozen=True)
class WritebackInvocation:
    """Prepared state passed from the shared sink runner to one sink body."""

    ctx: WritebackContext
    ops: dict[str, Any]
    client: Any | None
    result: WritebackResult
    dry_run: bool

    def unpack(self) -> InvocationParts:
        """Return sink-local values for a target-specific body."""
        return InvocationParts(
            self.ctx, self.ops, self.client, self.result, self.dry_run
        )


def resolve_writeback_client(
    ops: dict[str, Any],
    module: str,
    *,
    log: logging.Logger | None = None,
    label: str | None = None,
) -> Any | None:
    """Resolve an injected or connector-authenticated write-back client."""
    client = ops.get("client")
    if client is not None:
        return client
    try:
        connector = __import__(f"{module}.auth", fromlist=["get_client"])
        return connector.get_client()
    except Exception:
        (log or logger).debug(
            "%s write client unavailable", label or module, exc_info=True
        )
        return None


class WritebackClientMixin:
    """Shared injected/authenticated client resolution for write-back sinks."""

    domain: str
    client_module: str
    client_label: str | None = None

    def run(
        self, ctx: WritebackContext, ops: dict[str, Any], *, dry_run: bool
    ) -> WritebackResult:
        """Prepare one invocation, then dispatch its target-specific body."""
        result = WritebackResult(target=self.domain)
        client = self._client(ops)
        if client is None and not dry_run:
            result.skipped += 1
            return result
        return self._run(WritebackInvocation(ctx, ops, client, result, dry_run))

    def _run(self, invocation: WritebackInvocation) -> WritebackResult:
        """Apply target-specific operations; concrete sinks override this hook."""
        raise NotImplementedError  # ABSTRACT-OK

    def _client(self, ops: dict[str, Any]) -> Any | None:
        return resolve_writeback_client(
            ops,
            self.client_module,
            log=logging.getLogger(type(self).__module__),
            label=self.client_label,
        )


@runtime_checkable
class WritebackSink(Protocol):
    """A target system's write-back adapter.

    ``run`` applies the requested ``ops`` (inferences/enrichments/creations/
    retirements) when ``dry_run`` is False, else returns the *proposed* writes.
    """

    domain: str
    enable_flag: str
    # NOTE: an optional ``risk_tier`` attribute ("standard" default, or "high_stakes"
    # — never auto-execute, queued for approval) is read defensively via
    # ``getattr(sink, "risk_tier", "standard")`` below, so it is intentionally NOT a
    # required Protocol member (most sinks omit it).

    def run(
        self, ctx: WritebackContext, ops: dict[str, Any], *, dry_run: bool
    ) -> WritebackResult: ...


# ── sink registry (plugin pattern) ───────────────────────────────────────────

_SINKS: dict[str, WritebackSink] = {}


def register_sink(sink: WritebackSink) -> WritebackSink:
    """Register a write-back sink under its ``domain`` (idempotent)."""
    _require_identifier(sink.domain, "sink.domain")
    _require_identifier(sink.enable_flag, "sink.enable_flag")
    _SINKS[sink.domain] = sink
    return sink


def get_sink(domain: str) -> WritebackSink | None:
    return _SINKS.get((domain or "").lower().strip())


def list_sinks() -> list[str]:
    return sorted(_SINKS)


def __getattr__(name: str) -> Any:
    """Lazily expose ``run_writeback`` (it lives in ``runner``, which imports us)."""
    if name == "run_writeback":
        from .runner import run_writeback

        return run_writeback
    raise AttributeError(name)
