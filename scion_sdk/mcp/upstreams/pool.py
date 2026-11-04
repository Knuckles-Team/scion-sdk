"""The shared stdio process pool: one pooled child per upstream and key (R914.8).

The decision: one pooled subprocess per upstream and pool key, not one per
session. The framework default opens a fresh session per request, and a
per-session process would multiply by sessions times upstreams under a
long-lived HTTP server. An upstream application also keeps open-document state
in its process, and clients reconnect often. The ``principal`` scope keeps that
state from crossing principals.

Process shutdown defers to the transport's documented escalation (close, wait
out the grace window, kill the process tree); the pool bounds its own wait per
child by ``stop_grace_s`` so one stuck child cannot hold a shutdown open.

Pooled clients speak the handshake protocol era (``mode="legacy"``), not the
2026-07-28 era: the registry's elicitation pass-through (R914.10) forwards an
upstream's question over the back-channel, which the newer era removed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
from fastmcp import Client
from fastmcp.client.elicitation import ElicitationHandler
from fastmcp.client.transports import StdioTransport

from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_CIRCUIT_OPEN,
    UPSTREAM_POOL_FULL,
    UPSTREAM_SPAWN_FAILED,
    UpstreamError,
)

if TYPE_CHECKING:
    from scion_sdk.credentials.resolver import CredentialResolver
    from scion_sdk.mcp.upstreams.model import UpstreamDecl

__all__ = ["PoolSettings", "UpstreamPool", "current_principal"]

_logger = logging.getLogger(__name__)

_REAPER_TICK_SECONDS = 0.5


def current_principal() -> str:
    """The authenticated principal of the current session, or ``system``.

    A local (stdio) transport's actor is ``system``; an authenticated HTTP
    session carries its user.
    """
    try:
        from fastmcp.server.sessions import current_principal as _principal

        principal = _principal()
    except Exception:
        principal = None
    return str(principal) if principal is not None else "system"


@dataclass(frozen=True)
class PoolSettings:
    """The pool's knobs, with the spec's starting values and ranges."""

    idle_ttl_s: float = 900.0
    max_processes: int = 16
    stop_grace_s: float = 5.0
    restart_limit: int = 3
    restart_window_s: float = 60.0
    circuit_cooldown_s: float = 30.0
    call_timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if not 30 <= self.idle_ttl_s <= 86400:
            raise ValueError("idle_ttl_s is out of range 30..86400")
        if not 1 <= self.max_processes <= 64:
            raise ValueError("max_processes is out of range 1..64")
        if not 1 <= self.stop_grace_s <= 60:
            raise ValueError("stop_grace_s is out of range 1..60")
        if not 1 <= self.restart_limit <= 100:
            raise ValueError("restart_limit is out of range 1..100")
        if not 1 <= self.restart_window_s <= 3600:
            raise ValueError("restart_window_s is out of range 1..3600")
        if not 1 <= self.circuit_cooldown_s <= 3600:
            raise ValueError("circuit_cooldown_s is out of range 1..3600")
        if not 1 <= self.call_timeout_s <= 3600:
            raise ValueError("call_timeout_s is out of range 1..3600")


class _PooledStdioTransport(StdioTransport):
    """A stdio transport that reports each (re)spawn to the pool."""

    def __init__(
        self,
        command: str,
        args: list[str],
        *,
        on_restart: Callable[[], Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(command, args, **kwargs)
        self._on_restart = on_restart
        self._spawned = False

    async def connect(self, **kwargs: Any) -> Any:
        was_live = self._connect_task is not None and not self._is_session_dead()
        await super().connect(**kwargs)
        if not was_live and self._connect_task is not None:
            first_spawn = not self._spawned
            self._spawned = True
            if not first_spawn:
                self._on_restart()

    def is_alive(self) -> bool:
        return self._connect_task is not None and not self._is_session_dead()


@dataclass
class _PoolEntry:
    transport: _PooledStdioTransport | None = None
    client: Client[Any] | None = None
    token_path: Path | None = None
    last_used: float = 0.0
    restarts: list[float] = field(default_factory=list)
    circuit_open_until: float = 0.0
    restarts_total: int = 0


class UpstreamPool:
    """One shared stdio child per pool key, with restarts bounded by a circuit.

    The pool is built once at attach time; the server lifespan (through the
    gate) starts the reaper and :meth:`close` at shutdown. A spawn failure or a
    child that exits leaves the rest of the pool up; beyond the restart limit
    the key reports ``circuit_open`` until the cooldown ends.
    """

    def __init__(
        self,
        *,
        settings: PoolSettings,
        run_root: Path,
        resolver: CredentialResolver,
        inherited_env: tuple[str, ...],
        elicitation_forwarder: ElicitationHandler,
    ) -> None:
        self._settings = settings
        self._run_root = run_root
        self._resolver = resolver
        self._inherited_env = inherited_env
        self._forwarder = elicitation_forwarder
        self._entries: dict[str, _PoolEntry] = {}
        self._lock = anyio.Lock()
        self._reaper: Any = None
        self._reaper_refs = 0
        self._prober: Any = None

    # -- construction ---------------------------------------------------------

    def _spawn(self, key: str, entry: _PoolEntry, decl: UpstreamDecl) -> None:
        """Build the entry's transport and client; the child starts on connect."""
        from scion_sdk.mcp.upstreams.bridge import (
            build_bridge_command,
        )
        from scion_sdk.mcp.upstreams.stdio import (
            build_child_environment,
            build_stdio_command,
        )

        if decl.kind == "bridge":
            command, token_path = build_bridge_command(
                decl, resolver=self._resolver, run_root=self._run_root
            )
        else:
            command = build_stdio_command(decl)
            token_path = None
        environment = build_child_environment(
            decl, resolver=self._resolver, inherited=self._inherited_env
        )
        entry.transport = _PooledStdioTransport(
            command=command[0],
            args=command[1:],
            env=environment,
            keep_alive=True,
            on_restart=lambda: self._note_restart(key),
        )
        entry.token_path = token_path
        entry.client = Client(
            entry.transport,
            elicitation_handler=self._forwarder,
            mode="legacy",
            timeout=self._settings.call_timeout_s,
        )

    def _note_restart(self, key: str) -> None:
        entry = self._entries.get(key)
        if entry is None:
            return
        now = time.monotonic()
        entry.restarts_total += 1
        entry.restarts = [
            t for t in entry.restarts if now - t <= self._settings.restart_window_s
        ]
        entry.restarts.append(now)
        if len(entry.restarts) > self._settings.restart_limit:
            entry.circuit_open_until = now + self._settings.circuit_cooldown_s

    # -- acquisition ----------------------------------------------------------

    def pool_key(
        self, decl: UpstreamDecl, principal: str, scope: str | None = None
    ) -> str:
        effective = scope or decl.effective_pool_scope
        if effective == "server":
            return decl.name
        return f"{decl.name}:{principal}"

    def client_factory(self, decl: UpstreamDecl) -> Callable[[], Any]:
        """The proxy provider's client factory: the pooled client for the key."""

        async def factory() -> Client[Any]:
            principal = current_principal()
            return await self.acquire(decl, principal)

        return factory

    async def acquire(
        self, decl: UpstreamDecl, principal: str, scope: str | None = None
    ) -> Client[Any]:
        """The pooled client for ``decl`` and ``principal``, spawned if needed."""
        key = self.pool_key(decl, principal, scope)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                if self._live_count() >= self._settings.max_processes:
                    evicted = self._evict_lru_idle()
                    if not evicted:
                        raise UpstreamError(
                            UPSTREAM_POOL_FULL,
                            f"{decl.name}: pool holds {self._settings.max_processes} processes",
                        )
                entry = _PoolEntry()
                self._entries[key] = entry
            now = time.monotonic()
            if entry.circuit_open_until and entry.circuit_open_until > now:
                raise UpstreamError(UPSTREAM_CIRCUIT_OPEN, f"{decl.name}")
            if entry.transport is None or not entry.transport.is_alive():
                await self._stop_entry(entry)
                self._spawn(key, entry, decl)
            entry.last_used = now
            if entry.client is None:
                raise UpstreamError(
                    UPSTREAM_SPAWN_FAILED,
                    f"{decl.name}: pooled client missing after spawn",
                )
            return entry.client

    def _live_count(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.transport is not None)

    def _evict_lru_idle(self) -> bool:
        """Evict the least recently used *idle* (transport-less) entry slot."""
        idle = [
            (entry.last_used, key)
            for key, entry in self._entries.items()
            if entry.transport is None
        ]
        if not idle:
            return False
        idle.sort()
        self._entries.pop(idle[0][1])
        return True

    async def _stop_entry(self, entry: _PoolEntry) -> None:
        if entry.transport is not None:
            from scion_sdk.mcp.upstreams.bridge import remove_bridge_token

            with anyio.move_on_after(self._settings.stop_grace_s):
                await entry.transport.disconnect()  # type: ignore[no-untyped-call]
            remove_bridge_token(entry.token_path)
        entry.transport = None
        entry.client = None
        entry.token_path = None
        entry.circuit_open_until = 0.0

    # -- idle reaping ----------------------------------------------------------

    def attach_prober(self, prober: Any) -> None:
        """The active-namespace prober the reaper loop drives, if any."""
        self._prober = prober

    def start_reaper(self) -> None:
        self._reaper_refs += 1
        if self._reaper is None and self._reaper_refs > 0:
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def stop_reaper(self) -> None:
        self._reaper_refs = max(0, self._reaper_refs - 1)
        if self._reaper is not None and self._reaper_refs == 0:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reaper
            self._reaper = None

    async def _reap_loop(self) -> None:
        while True:
            await anyio.sleep(_REAPER_TICK_SECONDS)
            await self.reap_idle()
            if self._prober is not None:
                try:
                    await self._prober.probe_due()
                except Exception:
                    _logger.debug("upstream probe tick failed", exc_info=True)

    async def server_version(self, decl: UpstreamDecl, principal: str) -> str | None:
        """The upstream ``serverInfo`` version, from the pooled client when live."""
        entry = self._entries.get(self.pool_key(decl, principal))
        if entry is None or entry.client is None:
            return None
        session = getattr(entry.client, "session", None)
        info = getattr(session, "server_info", None)
        version = getattr(info, "version", None)
        return str(version) if version else None

    async def reap_idle(self) -> None:
        """Stop every pooled child idle longer than ``idle_ttl_s``."""
        now = time.monotonic()
        async with self._lock:
            for key, entry in list(self._entries.items()):
                if entry.transport is None:
                    del self._entries[key]
                    continue
                if now - entry.last_used >= self._settings.idle_ttl_s:
                    await self._stop_entry(entry)
                    del self._entries[key]

    # -- shutdown ---------------------------------------------------------------

    async def close(self) -> None:
        """Stop every child (SIGTERM escalation through the transport) and clean up."""
        await self.stop_reaper()
        async with self._lock:
            for entry in list(self._entries.values()):
                await self._stop_entry(entry)
            self._entries.clear()

    def restarts_total(self, key: str) -> int:
        entry = self._entries.get(key)
        return entry.restarts_total if entry is not None else 0

    def is_circuit_open(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return False
        return bool(
            entry.circuit_open_until and entry.circuit_open_until > time.monotonic()
        )
