"""Process write-back sink — Camunda & ARIS (CONCEPT:EG-KG.storage.nonblocking-checkpoint).

Pushes the KG's per-process intelligence (capability/code lineage, OWL inferences,
incidents, glossary/data lineage) onto live Camunda instances and ARIS models as a
hash-idempotent ``kg_intelligence`` payload. Folds the former ``process_writeback``
module onto the unified sink contract (fail-closed ``KG_PROCESS_WRITEBACK``,
dry-run-first).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..core import (
    WritebackContext,
    WritebackResult,
    register_sink,
    resolve_writeback_client,
)
from ._process_gather import _node_type, gather_intelligence
from ._process_push import (
    INTELLIGENCE_KEY,
    _is_empty,
    _push_aris,
    _push_camunda,
)

logger = logging.getLogger(__name__)

__all__ = [
    "INTELLIGENCE_KEY",
    "GraphComputeReader",
    "ProcessSink",
    "gather_intelligence",
    "push_process_intelligence",
]


def _process_targets(
    reader: Any, process_ids: Iterable[str] | None
) -> list[tuple[str, dict[str, Any]]]:
    if process_ids:
        return [(pid, reader.node_props(pid) or {}) for pid in process_ids]
    discover = getattr(reader, "process_nodes", None)
    return list(discover() or []) if callable(discover) else []


@dataclass
class _ProcessTargets:
    """Which writeback systems a process_id addresses, and their clients."""

    is_camunda: bool
    is_aris: bool
    camunda_client: Any | None
    aris_client: Any | None


def _record_dry_run_proposal(
    process_id: str, targets: _ProcessTargets, result: WritebackResult
) -> None:
    if (targets.is_camunda and targets.camunda_client is not None) or (
        targets.is_aris and targets.aris_client is not None
    ):
        result.proposals.append(
            {"op": "write_intelligence", "process": str(process_id)}
        )


def _dispatch_live_push(
    process_id: str,
    props: dict[str, Any],
    targets: _ProcessTargets,
    payload: dict[str, Any],
    result: WritebackResult,
) -> None:
    if targets.camunda_client is not None and targets.is_camunda:
        key = str(props.get("key") or str(process_id).split(":", 1)[-1])
        _push_camunda(targets.camunda_client, key, payload=payload, result=result)
    elif targets.aris_client is not None and targets.is_aris:
        _push_aris(
            targets.aris_client,
            str(process_id).split(":", 1)[-1],
            payload=payload,
            result=result,
        )


def _handle_one_process(
    reader: Any,
    process_id: str,
    props: dict[str, Any],
    *,
    clients: tuple[Any | None, Any | None],
    dry_run: bool,
    result: WritebackResult,
) -> None:
    camunda_client, aris_client = clients
    payload = gather_intelligence(reader, process_id)
    if _is_empty(payload):
        return
    targets = _ProcessTargets(
        is_camunda=str(process_id).startswith("bpmn_process:"),
        is_aris=str(process_id).startswith("aris_model:"),
        camunda_client=camunda_client,
        aris_client=aris_client,
    )
    if dry_run:
        _record_dry_run_proposal(process_id, targets, result)
        return
    _dispatch_live_push(process_id, props, targets, payload, result)


def push_process_intelligence(
    reader: Any,
    *,
    camunda_client: Any | None = None,
    aris_client: Any | None = None,
    process_ids: Iterable[str] | None = None,
    dry_run: bool = False,
    result: WritebackResult | None = None,
) -> WritebackResult:
    """Push KG process intelligence onto Camunda instances and/or ARIS models."""
    result = result or WritebackResult(target="process")
    if camunda_client is None and aris_client is None:
        return result
    for process_id, props in _process_targets(reader, process_ids):
        _handle_one_process(
            reader,
            process_id,
            props,
            clients=(camunda_client, aris_client),
            dry_run=dry_run,
            result=result,
        )
    return result


class GraphComputeReader:
    """Adapt the engine's ``graph_compute`` into the duck-typed process reader."""

    def __init__(self, engine: Any) -> None:
        self._gc = getattr(engine, "graph_compute", None)

    def node_props(self, node_id: str) -> dict[str, Any]:
        if self._gc is None:
            return {}
        try:
            return dict(self._gc._get_node_properties(node_id) or {})
        except Exception:
            return {}

    def out_edges(self, node_id: str) -> list[tuple[str, dict[str, Any]]]:
        if self._gc is None:
            return []
        try:
            return [(t, p) for _s, t, p in self._gc.out_edges(node_id, data=True)]
        except Exception:
            return []

    def in_edges(self, node_id: str) -> list[tuple[str, dict[str, Any]]]:
        if self._gc is None:
            return []
        try:
            return [(s, p) for s, _t, p in self._gc.in_edges(node_id, data=True)]
        except Exception:
            return []

    def process_nodes(self) -> list[tuple[str, dict[str, Any]]]:
        if self._gc is None:
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        try:
            for nid, props in self._gc.nodes(data=True):
                pid = str(nid)
                if (
                    pid.startswith(("bpmn_process:", "aris_model:"))
                    or _node_type(props) == "BusinessProcess"
                ):
                    out.append((pid, dict(props)))
        except Exception:
            return []
        return out


class ProcessSink:
    """Write-back sink for Camunda & ARIS process intelligence."""

    domain = "process"
    enable_flag = "KG_PROCESS_WRITEBACK"

    def run(
        self, ctx: WritebackContext, ops: dict[str, Any], *, dry_run: bool
    ) -> WritebackResult:
        result = WritebackResult(target=self.domain)
        target = str(ops.get("target") or "both").lower()
        camunda_client = ops.get("camunda_client")
        aris_client = ops.get("aris_client")
        if camunda_client is None and aris_client is None:
            if target in ("camunda", "both"):
                camunda_client = resolve_writeback_client({}, "camunda_mcp", log=logger)
            if target in ("aris", "both"):
                aris_client = resolve_writeback_client({}, "aris_mcp", log=logger)
        if camunda_client is None and aris_client is None:
            result.skipped += 1
            return result
        reader = GraphComputeReader(ctx.engine)
        return push_process_intelligence(
            reader,
            camunda_client=camunda_client,
            aris_client=aris_client,
            process_ids=ops.get("process_ids"),
            dry_run=dry_run,
            result=result,
        )


register_sink(ProcessSink())
