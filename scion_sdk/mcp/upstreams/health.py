"""Health and readiness per upstream (R914.9).

The registry tracks each upstream's state and serves it through the
read-only ``registry_status`` tool and the ``upstreams`` block of the
readiness body. ``inactive`` is a lazy upstream nobody activated, and it is
not a failure. A status holds an error code and never the error text of an
upstream. A probe runs only for active namespaces, every
``probe_interval_s``; a probe never wakes an inactive upstream.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

import anyio

from scion_sdk.connector_hooks import Readiness, ReadinessValue
from scion_sdk.mcp.upstreams.drift import (
    DriftReport,
    diff_tool_fingerprints,
    fingerprint_tools,
    tool_fingerprint_map,
)
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_CIRCUIT_OPEN,
    UPSTREAM_SPAWN_FAILED,
    UpstreamDecl,
    UpstreamStatus,
)
from scion_sdk.upstream_wire import UPSTREAMS_RESOURCE_URI

if TYPE_CHECKING:  # pragma: no cover
    from scion_sdk.connector_hooks import ReadinessProbe

__all__ = [
    "UPSTREAMS_RESOURCE_URI",
    "UpstreamProber",
    "UpstreamRegistry",
    "compose_readiness",
]

_logger = logging.getLogger(__name__)


class UpstreamRegistry:
    """The live status of every declared upstream, keyed by namespace."""

    def __init__(
        self,
        declarations: Sequence[UpstreamDecl],
        *,
        activation_setting: str,
        error_strategy: str,
    ) -> None:
        self._declarations = {decl.effective_namespace: decl for decl in declarations}
        self._activation_setting = activation_setting
        self._error_strategy = error_strategy
        self._statuses: dict[str, UpstreamStatus] = {}
        self._fingerprints: dict[str, dict[str, str]] = {}
        self._drift: dict[str, DriftReport] = {}
        self._lock = anyio.Lock()
        #: ``configured`` or ``not_configured`` when a declaration binds the
        #: artifact exchange (R914.13); ``None`` when none does.
        self.exchange_state: str | None = None
        for decl in declarations:
            state: Literal["disabled", "inactive"] = (
                "disabled" if not decl.enabled else "inactive"
            )
            self._statuses[decl.effective_namespace] = UpstreamStatus(
                name=decl.name,
                namespace=decl.effective_namespace,
                kind=decl.kind,
                state=state,
            )

    @property
    def error_strategy(self) -> str:
        return self._error_strategy

    def activation_for(self, decl: UpstreamDecl) -> str:
        """The declaration's activation, or the setting's (``lazy`` to start)."""
        return (
            decl.activation if decl.activation is not None else self._activation_setting
        )

    def declaration(self, namespace: str) -> UpstreamDecl | None:
        return self._declarations.get(namespace)

    def statuses(self) -> tuple[UpstreamStatus, ...]:
        """Every upstream's status, in declaration order."""
        return tuple(self._statuses[namespace] for namespace in self._declarations)

    def status(self, namespace: str) -> UpstreamStatus:
        return self._statuses[namespace]

    def drift_report(self, namespace: str) -> DriftReport | None:
        return self._drift.get(namespace)

    def drift_refuses(self, decl: UpstreamDecl) -> bool:
        """Whether the namespace is drifted under a ``refuse`` policy."""
        report = self._drift.get(decl.effective_namespace)
        return bool(report and report.is_drifted and decl.on_drift == "refuse")

    async def note_listed(
        self, decl: UpstreamDecl, tools: Sequence[Any], version: str | None = None
    ) -> DriftReport:
        """Record a successful ``tools/list``; returns the drift report, if any."""
        namespace = decl.effective_namespace
        mapping = tool_fingerprint_map(tools)
        fingerprint = fingerprint_tools(tools)
        async with self._lock:
            previous = self._fingerprints.get(namespace)
            expected = decl.expected_fingerprint
            if previous is None and expected is None:
                # First sighting is the baseline, not a change.
                report = DriftReport()
            else:
                report = diff_tool_fingerprints(previous, mapping)
            expected_mismatch = expected is not None and fingerprint != expected
            if expected_mismatch and not report.is_drifted:
                report = DriftReport(changed=tuple(sorted(mapping)))
            self._fingerprints[namespace] = mapping
            self._drift[namespace] = report
            drifted = report.is_drifted or expected_mismatch
            current = self._statuses[namespace]
            self._statuses[namespace] = current.model_copy(
                update={
                    "state": "drifted" if drifted else "ready",
                    "tool_count": len(tools),
                    "fingerprint": fingerprint,
                    "version": version if version is not None else current.version,
                    "last_error_code": None,
                    "last_ok_at": time.time(),
                    "restarts": current.restarts,
                }
            )
        return report

    async def note_failure(self, decl: UpstreamDecl, code: str) -> None:
        """Record a refused or failed interaction with the upstream."""
        namespace = decl.effective_namespace
        state = "circuit_open" if code == UPSTREAM_CIRCUIT_OPEN else "unavailable"
        async with self._lock:
            current = self._statuses[namespace]
            self._statuses[namespace] = current.model_copy(
                update={
                    "state": state,
                    "last_error_code": code,
                    "tool_count": current.tool_count,
                    "fingerprint": current.fingerprint,
                }
            )

    def readiness_block(self) -> dict[str, Any]:
        """The ``upstreams`` block of the readiness body and the resource."""
        block: list[dict[str, Any]] = []
        for namespace, status in self._statuses.items():
            entry = status.model_dump(mode="json")
            entry["expected_fingerprint"] = self._declarations[
                namespace
            ].expected_fingerprint
            block.append(entry)
        result: dict[str, Any] = {"upstreams": block}
        if self.exchange_state is not None:
            result["exchange"] = self.exchange_state
        return result

    def has_fatal(self) -> bool:
        """An enabled upstream in ``unavailable`` or ``circuit_open``."""
        for namespace, status in self._statuses.items():
            decl = self._declarations[namespace]
            if not decl.enabled:
                continue
            if status.state in ("unavailable", "circuit_open"):
                return True
        return False


def compose_readiness(
    probe: ReadinessProbe, registry: UpstreamRegistry
) -> ReadinessProbe:
    """The connector's readiness probe with the ``upstreams`` block composed in.

    Under ``warn`` no upstream state makes the server unready. Under ``raise``
    an enabled upstream in ``unavailable`` or ``circuit_open`` answers 503.
    """

    async def composed() -> Readiness:
        base = await probe()
        detail: Mapping[str, ReadinessValue] = {
            **base.detail,
            **registry.readiness_block(),
        }
        ready = base.ready
        if registry.error_strategy == "raise" and registry.has_fatal():
            ready = False
        return Readiness(ready=ready, detail=detail)

    return composed


class UpstreamProber:
    """A background probe for active namespaces; it never wakes an inactive one.

    Each probe re-lists its namespace through the given probe function, which
    returns ``(tools, version)``. A failure marks the upstream ``unavailable``
    (the code is a refusal token, never error text); a success refreshes its
    status. The prober runs from the pool reaper loop: it probes each
    namespace at most once per ``interval_s``.
    """

    def __init__(
        self,
        registry: UpstreamRegistry,
        probes: Sequence[
            tuple[
                UpstreamDecl, Callable[[], Awaitable[tuple[Sequence[Any], str | None]]]
            ]
        ],
        *,
        interval_s: float,
    ) -> None:
        self._registry = registry
        self._probes = list(probes)
        self._interval_s = interval_s
        self._last_probe: dict[str, float] = {}

    async def probe_due(self) -> None:
        """Probe every namespace whose interval has elapsed, at most once each."""
        import time

        now = time.monotonic()
        for decl, probe_fn in self._probes:
            namespace = decl.effective_namespace
            if now - self._last_probe.get(namespace, 0.0) < self._interval_s:
                continue
            self._last_probe[namespace] = now
            try:
                tools, version = await probe_fn()
            except Exception as exc:
                code = getattr(exc, "code", None) or UPSTREAM_SPAWN_FAILED
                await self._registry.note_failure(decl, code)
                continue
            try:
                await self._registry.note_listed(decl, tools, version)
            except Exception:
                await self._registry.note_failure(decl, UPSTREAM_SPAWN_FAILED)
