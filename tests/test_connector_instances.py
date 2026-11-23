"""Named upstream instances per connector (SCION-CENTRALIZED-CAPABILITIES-R050)."""

from __future__ import annotations

import inspect
import json
from functools import partial
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from scion_sdk.config.instances import (
    ConnectorInstances,
    InstanceEntry,
    UnknownInstanceError,
)
from scion_sdk.credentials.resolution import (
    default_credential_resolver,
    resolve_secret_reference,
)
from scion_sdk.entitlements import EntitlementError
from scion_sdk.identity import ANONYMOUS_ACTOR, ActorContext, ActorType, use_actor
from scion_sdk.mcp.tool_surface import register_tool_surface, registered_tools


class _RecordedClient:
    """A stand-in connector client that records how it was built."""

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.kwargs = kwargs

    def list_users(self) -> list[str]:
        """List users; the result names the instance it was built for."""
        return [self.base_url]


def _resolver() -> Any:
    return partial(resolve_secret_reference, resolver=default_credential_resolver())


def _set_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CRM_INSTANCES",
        json.dumps(
            {
                "prod": {
                    "base_url": "https://prod.example.com",
                    "route_host": "prod.example.com",
                    "access_headers": {"X-Service-Token": "env://CRM_PROD_TOKEN"},
                    "tls_profile_ref": "openbao://secret/data/crm/prod?field=profile",
                },
                "staging": {"base_url": "https://staging.example.com"},
            }
        ),
    )
    monkeypatch.setenv("CRM_PROD_TOKEN", "prod-secret")


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
def test_named_instances_resolve_default_and_refuse_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_map(monkeypatch)
    instances = ConnectorInstances(
        "crm", client_cls=_RecordedClient, resolver=_resolver()
    )

    # The map, in entry order.
    assert instances.names == ("prod", "staging")
    assert instances.default == "prod"
    assert instances.entry("staging") == InstanceEntry(
        base_url="https://staging.example.com"
    )

    # The named default.
    monkeypatch.setenv("CRM_DEFAULT_INSTANCE", "staging")
    named = ConnectorInstances("crm", client_cls=_RecordedClient, resolver=_resolver())
    assert named.default == "staging"

    # A client is built from the entry: URL, route host, resolved access
    # headers and the TLS profile reference.
    client = instances.client_for("prod")
    assert client.base_url == "https://prod.example.com"
    assert client.kwargs == {
        "route_host": "prod.example.com",
        "headers": {"X-Service-Token": "prod-secret"},
        "tls_profile_ref": "openbao://secret/data/crm/prod?field=profile",
    }

    # An unknown name refuses with a typed error that lists names only.
    with pytest.raises(UnknownInstanceError) as exc:
        instances.client_for("qa")
    message = str(exc.value)
    assert exc.value.name == "qa"
    assert exc.value.known == ("prod", "staging")
    assert "prod" in message and "staging" in message
    assert "example.com" not in message


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
def test_first_entry_is_the_default_without_a_named_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_map(monkeypatch)
    instances = ConnectorInstances(
        "crm", client_cls=_RecordedClient, resolver=_resolver()
    )
    assert instances.default == "prod"
    assert instances.client_for().base_url == "https://prod.example.com"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
def test_access_headers_require_a_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_map(monkeypatch)
    with pytest.raises(Exception) as exc:
        ConnectorInstances("crm", client_cls=_RecordedClient)
    assert "resolver" in str(exc.value)
    monkeypatch.setenv("CRM_INSTANCES", '{"prod": {"base_url": "https://p.example"}}')
    assert (
        ConnectorInstances("crm", client_cls=_RecordedClient).client_for().base_url
        == "https://p.example"
    )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
def test_single_url_fallback_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("CRM_INSTANCES", "CRM_DEFAULT_INSTANCE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example.com")
    monkeypatch.setenv("CRM_ROUTE_HOST", "crm.example.com")
    monkeypatch.setenv(
        "CRM_ACCESS_HEADERS", json.dumps({"X-Service-Token": "env://CRM_TOKEN"})
    )
    monkeypatch.setenv("CRM_TOKEN", "s3cret")
    monkeypatch.setenv("CRM_TLS_PROFILE_REF", "openbao://secret/data/crm?field=p")

    instances = ConnectorInstances(
        "crm", client_cls=_RecordedClient, resolver=_resolver()
    )
    assert instances.names == ("default",)
    assert instances.default == "default"
    client = instances.client_for()
    assert client.base_url == "https://crm.example.com"
    assert client.kwargs == {
        "route_host": "crm.example.com",
        "headers": {"X-Service-Token": "s3cret"},
        "tls_profile_ref": "openbao://secret/data/crm?field=p",
    }


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
def test_no_upstream_at_all_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CRM_INSTANCES",
        "CRM_BASE_URL",
        "CRM_ROUTE_HOST",
        "CRM_ACCESS_HEADERS",
        "CRM_TLS_PROFILE_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    instances = ConnectorInstances("crm", client_cls=_RecordedClient)
    with pytest.raises(Exception) as exc:
        instances.client_for()
    assert "CRM_INSTANCES" in str(exc.value) and "CRM_BASE_URL" in str(exc.value)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
async def test_tool_surface_advertises_the_instance_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_map(monkeypatch)
    instances = ConnectorInstances(
        "crm", client_cls=_RecordedClient, resolver=_resolver()
    )
    default_client = _RecordedClient(base_url="https://default.example.com")
    mcp: FastMCP[Any] = FastMCP("crm-mcp")
    register_tool_surface(
        mcp,
        service="crm-mcp",
        client_cls=_RecordedClient,
        get_client=lambda: default_client,
        instances=instances,
    )
    tool = registered_tools(mcp)["crm_list_users"]

    # The advertised argument sits on the tool signature, like ``ctx``.
    parameters = inspect.signature(tool.fn).parameters
    assert "instance" in parameters
    assert parameters["instance"].default is None

    # No instance: the connector's default client serves.
    assert await tool.fn() == ["https://default.example.com"]

    # A named instance: its client serves, and the result names it.
    assert await tool.fn(instance="staging") == ["https://staging.example.com"]

    # An unknown instance refuses through the tool as well.
    with pytest.raises(UnknownInstanceError):
        await tool.fn(instance="qa")


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
async def test_advertised_instance_crosses_the_mcp_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_map(monkeypatch)
    instances = ConnectorInstances(
        "crm", client_cls=_RecordedClient, resolver=_resolver()
    )
    mcp: FastMCP[Any] = FastMCP("crm-mcp")
    register_tool_surface(
        mcp,
        service="crm-mcp",
        client_cls=_RecordedClient,
        get_client=lambda: _RecordedClient(base_url="https://default.example.com"),
        instances=instances,
    )
    async with Client(mcp) as client:
        result = await client.call_tool("crm_list_users", {"instance": "staging"})
    assert json.loads(result.content[0].text) == ["https://staging.example.com"]


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
def test_unopted_surface_advertises_no_instance_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_map(monkeypatch)
    mcp: FastMCP[Any] = FastMCP("crm-mcp")
    register_tool_surface(
        mcp,
        service="crm-mcp",
        client_cls=_RecordedClient,
        get_client=lambda: _RecordedClient(base_url="https://default.example.com"),
    )
    tool = registered_tools(mcp)["crm_list_users"]
    assert "instance" not in inspect.signature(tool.fn).parameters


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R050")
async def test_instance_entitlement_applies_per_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_map(monkeypatch)
    instances = ConnectorInstances(
        "crm", client_cls=_RecordedClient, resolver=_resolver()
    )
    assert instances.namespace == "crm"
    mcp: FastMCP[Any] = FastMCP("crm-mcp")
    register_tool_surface(
        mcp,
        service="crm-mcp",
        client_cls=_RecordedClient,
        get_client=lambda: _RecordedClient(base_url="https://default.example.com"),
        instances=instances,
    )
    tool = registered_tools(mcp)["crm_list_users"]
    operator = ActorContext(
        actor_id="operator",
        actor_type=ActorType.AUTOMATED_SERVICE,
        tenant_id="tenant-1",
        roles=("crm:prod",),
        authenticated=True,
    )

    # The caller reaches the instance its capability names...
    with use_actor(operator):
        assert await tool.fn(instance="prod") == ["https://prod.example.com"]

    # ...and is refused for the one it cannot reach, named.
    with use_actor(operator), pytest.raises(EntitlementError) as exc:
        await tool.fn(instance="staging")
    assert exc.value.reason == "not_entitled"
    assert "crm:staging" in str(exc.value)

    # An anonymous caller is refused before any instance is built.
    with use_actor(ANONYMOUS_ACTOR), pytest.raises(EntitlementError) as exc:
        await tool.fn(instance="prod")
    assert exc.value.reason == "anonymous_caller"

    # The default client path carries no new gate: a deployment that sets
    # no map is unchanged.
    monkeypatch.delenv("CRM_INSTANCES", raising=False)
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example.com")
    plain = ConnectorInstances("crm", client_cls=_RecordedClient)
    with use_actor(ANONYMOUS_ACTOR):
        assert plain.client_for().base_url == "https://crm.example.com"
    assert await tool.fn() == ["https://default.example.com"]
