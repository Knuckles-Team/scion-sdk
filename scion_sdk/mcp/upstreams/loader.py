"""Attach the declared upstreams to the connector server (R914.5).

``attach_upstreams`` turns declarations into namespaced providers on the
connector's own server: one proxy provider per enabled upstream, each wrapped
in its namespace transform, the ``ns:<namespace>`` tag and a session
activation gate, then mounted on one aggregate provider. The registry adds the
four read-only tools and the ``connector://upstreams`` resource. A connector
with no declaration and no overlay file pays nothing and serves exactly what
it served before.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from fastmcp.server.providers import AggregateProvider, Provider, ProxyProvider
from fastmcp.server.transforms import Visibility

from scion_sdk.config import csv_values, setting
from scion_sdk.credentials.resolver import EnvironmentCredentialResolver
from scion_sdk.mcp.tool_surface import registered_tools
from scion_sdk.mcp.upstreams.activation import (
    REGISTRY_TOOL_NAMES,
    attach_registry_tools,
)
from scion_sdk.mcp.upstreams.elicitation import make_elicitation_forwarder
from scion_sdk.mcp.upstreams.gate import ActivationGate
from scion_sdk.mcp.upstreams.health import (
    UPSTREAMS_RESOURCE_URI,
    UpstreamProber,
    UpstreamRegistry,
)
from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_SPAWN_FAILED,
    UPSTREAM_STRATEGY_INVALID,
    UPSTREAM_TOOL_COLLISION,
    UpstreamDecl,
    UpstreamDeclError,
    UpstreamError,
)
from scion_sdk.mcp.upstreams.overlay import merge_overlay
from scion_sdk.mcp.upstreams.pool import PoolSettings, UpstreamPool
from scion_sdk.mcp.upstreams.tagging import NamespaceTag

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["REGISTRY_SETTING_PREFIX", "attach_upstreams", "read_registry_settings"]

REGISTRY_SETTING_PREFIX = "SCION_UPSTREAMS"

_logger = logging.getLogger(__name__)


def _choice[C](key: str, default: C, allowed: tuple[str, ...]) -> C:
    value = setting(key, default)
    if value not in allowed:
        raise UpstreamDeclError(UPSTREAM_STRATEGY_INVALID, f"{key}={value!r}")
    return cast(C, value)


@dataclass(frozen=True)
class RegistrySettings:
    """The registry's knobs, read from the environment with bounded defaults."""

    activation: Literal["lazy", "active"] = "lazy"
    error_strategy: Literal["warn", "raise"] = "warn"
    connect_timeout_s: float = 10.0
    call_timeout_s: float = 120.0
    list_cache_ttl_s: float = 60.0
    inherit_env: tuple[str, ...] = ("PATH", "HOME", "LANG")
    max_active_namespaces: int = 4
    idle_ttl_s: float = 900.0
    max_processes: int = 16
    stop_grace_s: float = 5.0
    restart_limit: int = 3
    restart_window_s: float = 60.0
    circuit_cooldown_s: float = 30.0
    probe_interval_s: float = 60.0


def read_registry_settings() -> RegistrySettings:
    """Read the registry knobs; an unknown strategy refuses at startup."""
    return RegistrySettings(
        activation=_choice("SCION_UPSTREAM_ACTIVATION", "lazy", ("lazy", "active")),
        error_strategy=_choice(
            "SCION_UPSTREAM_ERROR_STRATEGY", "warn", ("warn", "raise")
        ),
        connect_timeout_s=setting("SCION_UPSTREAMS_CONNECT_TIMEOUT_S", 10.0),
        call_timeout_s=setting("SCION_UPSTREAMS_CALL_TIMEOUT_S", 120.0),
        list_cache_ttl_s=setting("SCION_UPSTREAMS_LIST_CACHE_TTL_S", 60.0),
        inherit_env=tuple(
            csv_values(setting("SCION_UPSTREAMS_INHERIT_ENV", "PATH,HOME,LANG"))
        ),
        max_active_namespaces=setting("SCION_UPSTREAMS_MAX_ACTIVE_NAMESPACES", 4),
        idle_ttl_s=setting("SCION_UPSTREAMS_IDLE_TTL_S", 900.0),
        max_processes=setting("SCION_UPSTREAMS_MAX_PROCESSES", 16),
        stop_grace_s=setting("SCION_UPSTREAMS_STOP_GRACE_S", 5.0),
        restart_limit=setting("SCION_UPSTREAMS_RESTART_LIMIT", 3),
        restart_window_s=setting("SCION_UPSTREAMS_RESTART_WINDOW_S", 60.0),
        circuit_cooldown_s=setting("SCION_UPSTREAMS_CIRCUIT_COOLDOWN_S", 30.0),
        probe_interval_s=setting("SCION_UPSTREAMS_PROBE_INTERVAL_S", 60.0),
    )


def _client_factory(
    decl: UpstreamDecl,
    *,
    pool: UpstreamPool | None,
    settings: RegistrySettings,
    resolver: EnvironmentCredentialResolver,
    forwarder: Any,
) -> Callable[[], Any]:
    if decl.kind == "http":
        from fastmcp import Client

        from scion_sdk.mcp.upstreams.http import build_http_transport

        async def http_factory() -> Any:
            transport = build_http_transport(decl, resolver=resolver)
            return Client(
                transport,
                elicitation_handler=forwarder,
                timeout=settings.call_timeout_s,
                init_timeout=settings.connect_timeout_s,
            )

        return http_factory

    async def stdio_factory() -> Any:
        if pool is None:
            # Unreachable: a stdio pool is built before any factory, but fail
            # closed rather than assert away under optimised bytecode.
            raise UpstreamError(
                UPSTREAM_SPAWN_FAILED,
                f"{decl.name}: no stdio pool for this declaration",
            )
        from scion_sdk.mcp.upstreams.pool import current_principal

        return await pool.acquire(decl, current_principal())

    return stdio_factory


def _check_collisions(server: Any, declarations: Any) -> None:
    """A namespace owns its name prefix; the registry tools' names are reserved."""
    local = set(registered_tools(server))
    reserved = set(REGISTRY_TOOL_NAMES)
    for name in local:
        if name in reserved:
            raise UpstreamDeclError(
                UPSTREAM_TOOL_COLLISION, f"local tool {name!r} is reserved"
            )
    for decl in declarations:
        if not decl.enabled:
            continue
        prefix = f"{decl.effective_namespace}_"
        for name in local:
            if name.startswith(prefix):
                raise UpstreamDeclError(
                    UPSTREAM_TOOL_COLLISION,
                    f"local tool {name!r} collides with namespace {decl.effective_namespace!r}",
                )


def attach_upstreams(
    server: FastMCP[Any],
    declarations: Sequence[UpstreamDecl],
) -> UpstreamRegistry | None:
    """Mount the upstreams on ``server``; returns the registry, or ``None``.

    A connector with no declaration and no overlay file serves exactly what
    it served before.

    Raises:
        UpstreamDeclError: a declaration, overlay entry, strategy or tool
            collision refuses at startup.
    """
    base = tuple(declarations)
    overlay_file = setting(f"{REGISTRY_SETTING_PREFIX}_FILE")
    disabled = csv_values(setting(f"{REGISTRY_SETTING_PREFIX}_DISABLED", ""))
    try:
        merged = merge_overlay(base, overlay_file=overlay_file, disabled=disabled)
    except UpstreamDeclError:
        if not base:
            raise
        _logger.warning("upstream overlay refused; serving the connector declarations")
        from scion_sdk.mcp.upstreams.model import validate_declarations

        merged = validate_declarations(base)
    if not merged:
        return None

    from scion_sdk.mcp.upstreams.exchange import (
        bind_exchange_roots,
        load_exchange_runtime,
    )

    exchange = load_exchange_runtime(merged, getattr(server, "name", "") or "connector")
    merged = tuple(bind_exchange_roots(decl, exchange) for decl in merged)
    knobs = read_registry_settings()
    registry = UpstreamRegistry(
        merged, activation_setting=knobs.activation, error_strategy=knobs.error_strategy
    )
    _check_collisions(server, merged)
    if any(decl.exchange is not None for decl in merged):
        registry.exchange_state = "configured" if exchange else "not_configured"

    resolver = EnvironmentCredentialResolver()
    forwarder = make_elicitation_forwarder()
    pool: UpstreamPool | None = None
    if any(decl.kind in ("stdio", "bridge") for decl in merged if decl.enabled):
        run_root = Path(tempfile.mkdtemp(prefix="scion-upstreams-"))
        pool = UpstreamPool(
            settings=PoolSettings(
                idle_ttl_s=knobs.idle_ttl_s,
                max_processes=knobs.max_processes,
                stop_grace_s=knobs.stop_grace_s,
                restart_limit=knobs.restart_limit,
                restart_window_s=knobs.restart_window_s,
                circuit_cooldown_s=knobs.circuit_cooldown_s,
                call_timeout_s=knobs.call_timeout_s,
            ),
            run_root=run_root,
            resolver=resolver,
            inherited_env=knobs.inherit_env,
            elicitation_forwarder=forwarder,
        )

    aggregate = AggregateProvider(provider_error_strategy=knobs.error_strategy)
    # The connector server is itself the outer aggregate: under ``raise`` a
    # failing upstream must fail the whole list, which the root decides.
    server.provider_error_strategy = knobs.error_strategy
    lazy_tags: set[str] = set()
    gates: list[tuple[UpstreamDecl, ActivationGate]] = []
    for decl in merged:
        if not decl.enabled:
            continue
        activation = registry.activation_for(decl)
        factory = _client_factory(
            decl, pool=pool, settings=knobs, resolver=resolver, forwarder=forwarder
        )
        cache_ttl = knobs.list_cache_ttl_s
        provider: Provider = ProxyProvider(factory, cache_ttl=cache_ttl)
        provider = provider.wrap_transform(NamespaceTag(decl))
        gate = ActivationGate(
            provider,
            decl,
            registry=registry,
            activation=activation,
            pool=pool if decl.kind in ("stdio", "bridge") else None,
            exchange=exchange,
        )
        aggregate.add_provider(gate, namespace=decl.effective_namespace)
        gates.append((decl, gate))
        if activation == "lazy":
            lazy_tags.add(f"ns:{decl.effective_namespace}")

    server.add_provider(aggregate)
    if lazy_tags:
        server.add_transform(Visibility(False, tags=lazy_tags))

    attach_registry_tools(
        server, registry, max_active_namespaces=knobs.max_active_namespaces
    )

    @server.resource(UPSTREAMS_RESOURCE_URI, mime_type="application/json")
    def _upstreams_resource() -> dict[str, Any]:
        """The live status of every declared upstream, for readiness and certify."""
        return registry.readiness_block()

    active_probes = [
        (decl, gate.probe)
        for decl, gate in gates
        if registry.activation_for(decl) == "active"
    ]
    if pool is not None and active_probes:
        pool.attach_prober(
            UpstreamProber(registry, active_probes, interval_s=knobs.probe_interval_s)
        )
    return registry
