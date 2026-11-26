"""Create a connector MCP server with the fleet's standard policy.

Extracted from ``agent_utilities.mcp.server_factory.create_mcp_server``. It keeps
the fleet-facing contract (``create_mcp_server`` returns
``(args, mcp, middlewares)``; the same flags and environment defaults; fail
closed for exposed listeners) and leaves Meristem-internal wiring behind: the
WorkItem tasks extension, engine-backed ``/metrics``, semantic tool filtering
through the knowledge graph, and delegation and Eunomia middleware.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from scion_sdk.adapters.platform_fallback import PlatformFallback
    from scion_sdk.vendor_extractors.enterprise_dispatch import (
        EnterpriseDispatcher,
    )

from scion_sdk.config import setting
from scion_sdk.credentials.resolver import CredentialResolver
from scion_sdk.mcp.auth.factory import configure_auth
from scion_sdk.mcp.auth.policy import AuthConfigurationError
from scion_sdk.mcp.change_events import serve_change_subscriptions
from scion_sdk.mcp.content import ConnectorContent, register_connector_content
from scion_sdk.mcp.exposure import NetworkExposureError
from scion_sdk.mcp.middleware import build_middleware
from scion_sdk.mcp.network import build_network_serving_config
from scion_sdk.mcp.parser import TRANSPORTS, create_mcp_parser
from scion_sdk.mcp.registry import (
    ServerRegistry,
    endpoint_reference,
    lease_ttl_seconds,
    registration_lifespan,
)
from scion_sdk.mcp.served_security import (
    ServedSecurityError,
    ServedSecurityOverride,
    apply_served_security_profile,
)
from scion_sdk.mcp.tool_paging import install_tool_paging
from scion_sdk.mcp.visibility import VisibilityTransform
from scion_sdk.mcp.visibility_policy import VisibilityPolicy

__all__ = ["create_mcp_server"]

_logger = logging.getLogger(__name__)


def _parse_arguments(
    parser: argparse.ArgumentParser, command_args: list[str] | None
) -> argparse.Namespace:
    args, _ = parser.parse_known_args(command_args)
    if args.help:
        parser.print_help(sys.stderr)
        raise SystemExit(0)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    return args


def _authentication(
    args: argparse.Namespace, resolver: CredentialResolver | None
) -> tuple[Any, bool]:
    """The auth provider, and whether the operator explicitly chose auth type none."""
    try:
        build_network_serving_config(args)
        no_auth = args.auth_type == "none"
        return configure_auth(args, resolver=resolver), no_auth
    except (NetworkExposureError, AuthConfigurationError) as exc:
        _logger.error("Refusing to build MCP server: %s", exc)
        raise SystemExit(1) from exc


def _lifespan(
    args: argparse.Namespace, *, name: str, registry: ServerRegistry | None
) -> Callable[[Any], AbstractAsyncContextManager[None]] | None:
    if registry is None or not setting("MCP_FLEET_REGISTRATION", True):
        return None
    url = endpoint_reference(
        name, transport=args.transport, host=args.host, port=args.port
    )
    return registration_lifespan(
        registry, name=name, url=url, ttl_secs=lease_ttl_seconds()
    )


def _health_for(fixture_mode: bool) -> Callable[[Request], Any]:
    async def _health(request: Request) -> JSONResponse:
        body: dict[str, Any] = {"status": "ok"}
        if fixture_mode:
            # R931: the served descriptor says these are recorded answers.
            body["fixture_mode"] = True
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    return _health


def create_mcp_server(
    name: str,
    *,
    version: str,
    instructions: str = "",
    command_args: list[str] | None = None,
    transport_choices: Sequence[str] = TRANSPORTS,
    content: ConnectorContent | None = None,
    server_registry: ServerRegistry | None = None,
    credential_resolver: CredentialResolver | None = None,
    served_security_override: ServedSecurityOverride | None = None,
    platform_fallback: PlatformFallback | None = None,
    enterprise_dispatcher: EnterpriseDispatcher | None = None,
) -> tuple[argparse.Namespace, FastMCP[Any], list[Any]]:
    """Build a connector FastMCP server.

    Args:
        name: Server name reported to clients and registered with the fleet.
        version: Server version reported to clients.
        instructions: Server instructions for models.
        command_args: Arguments to parse instead of ``sys.argv``.
        transport_choices: Transports this server offers.
        content: Connector content to serve as MCP primitives. When omitted,
            no packaged content is served; ``run()`` and ``build_server`` pass
            the directories from ``content_dirs(package)``.
        server_registry: Registry to renew this server's lease in; the lease
            runs only when ``MCP_FLEET_REGISTRATION`` is true (the default).
        credential_resolver: Resolver for secret references in auth flags.
        served_security_override: Explicit, logged opt-out of the served
            security profile that every network transport otherwise gets.
        platform_fallback: Optional host-authorized read/search fallback to serve.
        enterprise_dispatcher: Optional host-authorized enterprise operation handler.

    Returns:
        ``(args, mcp, middlewares)``; the caller adds the middlewares.

    Raises:
        SystemExit: on ``--help``, an invalid port, unsafe network exposure or
            invalid authentication settings, or a network transport
            without the served security profile.
    """
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)
    args = _parse_arguments(
        create_mcp_parser(transport_choices=transport_choices), command_args
    )
    auth, no_auth = _authentication(args, credential_resolver)
    mcp: FastMCP[Any] = FastMCP(
        name,
        instructions=instructions,
        version=version,
        auth=auth,
        lifespan=_lifespan(args, name=name, registry=server_registry),
    )
    try:
        apply_served_security_profile(
            args.transport,
            auth_provider=mcp.auth,
            mcp=mcp,
            audience=args.token_audience,
            override=served_security_override,
            explicit_no_auth=no_auth,
        )
    except ServedSecurityError as exc:
        _logger.error("Refusing to build MCP server: %s", exc)
        raise SystemExit(1) from exc
    mcp.custom_route("/health", methods=["GET"])(_health_for(bool(args.fixture_mode)))
    mcp.add_transform(
        VisibilityTransform(
            VisibilityPolicy.from_settings(args.tools, args.disabled_tools)
        )
    )
    install_tool_paging(mcp)
    _register_native_primitives(mcp, name)
    serve_change_subscriptions(mcp)
    if platform_fallback is not None:
        from scion_sdk.mcp.platform_tools import register_platform_tools

        register_platform_tools(mcp, platform_fallback)
    if enterprise_dispatcher is not None:
        from scion_sdk.mcp.enterprise_tools import register_enterprise_tools

        register_enterprise_tools(mcp, enterprise_dispatcher)
    if content is not None:
        register_connector_content(mcp, content)
    middlewares = build_middleware(no_auth=no_auth, connector_id=name)
    if args.fixture_mode:
        middlewares.append(_fixture_middleware(content))
    return args, mcp, middlewares


def _fixture_middleware(content: ConnectorContent | None) -> Any:
    """The fixture dispatcher for ``--fixture-mode`` (SCION-CONNECTOR-CONTROL-R931).

    The fixtures live in the connector package's ``fixtures/`` directory. A
    connector with no content directory, or no fixtures, refuses to start in
    this mode rather than serving nothing.
    """
    from scion_sdk.mcp.fixture_mode import (
        FIXTURE_DIRECTORY,
        FixtureMiddleware,
        FixtureStore,
    )

    if content is None:
        _logger.error("Refusing fixture mode: the connector declares no content root")
        raise SystemExit(1)
    store = FixtureStore.load(content.package_root / FIXTURE_DIRECTORY)
    if not len(store):
        _logger.error("Refusing fixture mode: the connector ships no fixtures")
        raise SystemExit(1)
    return FixtureMiddleware(store)


def _register_native_primitives(mcp: FastMCP[Any], name: str) -> None:
    """Register native prompt/resource/resource-template primitives.

    ARBORA-FLEET-R007.1: every connector server answers a native prompt
    list, a native resource read and a native resource-template list,
    independent of any connector content and alongside its ``.tool()``/
    Skills-over-MCP registrations.
    """

    @mcp.prompt(name="connector-status")
    def _connector_status_prompt() -> str:
        """Ask for this connector server's current operational status."""
        return f"Report the operational status of the {name} connector server."

    @mcp.resource("connector://status", mime_type="application/json")
    def _connector_status_resource() -> dict[str, str]:
        """Native resource exposing this server's static status payload."""
        return {"server": name, "status": "ok"}

    @mcp.resource("connector://capability/{capability}", mime_type="application/json")
    def _connector_capability_resource(capability: str) -> dict[str, str]:
        """Native resource template describing one capability by name."""
        return {"server": name, "capability": capability}
