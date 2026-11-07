"""The actor a served MCP request carries, from the credential its transport holds.

The transport only supplies the credential; :func:`request_actor` turns it into
an actor (see :mod:`scion_sdk.mcp.actor_binding` for the middleware that binds
it). This module needs the ``[mcp]`` extra.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastmcp.server.dependencies import get_access_token, get_http_request

from scion_sdk.config import csv_values, setting
from scion_sdk.identity import (
    ANONYMOUS_ACTOR,
    SYSTEM_ACTOR,
    ActorContext,
    ActorType,
)

__all__ = [
    "PRINCIPAL_GRANTS_SETTING",
    "request_actor",
    "token_actor",
]

#: Operator config granting capabilities to token principals, for example
#: ``arbora=ssh:*;backup-bot=ssh:nas,ssh:lab``. A principal matches the token's
#: ``sub``, ``client_id``, ``azp`` or ``preferred_username``.
PRINCIPAL_GRANTS_SETTING = "CONNECTOR_PRINCIPAL_GRANTS"

_ROLE_KEYS = ("roles", "role")
_GROUP_KEYS = ("groups", "group")
_TENANT_KEYS = ("tenant_id", "tenant", "org_id", "tid", "org")
_SUBJECT_KEYS = ("sub", "client_id", "azp")
_PRINCIPAL_KEYS = (*_SUBJECT_KEYS, "preferred_username")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _roles(claims: Mapping[str, Any]) -> list[str]:
    roles = [role for key in _ROLE_KEYS for role in _strings(claims.get(key))]
    realm = claims.get("realm_access")
    if isinstance(realm, Mapping):
        roles.extend(_strings(realm.get("roles")))
    clients = claims.get("resource_access")
    if isinstance(clients, Mapping):
        for block in clients.values():
            if isinstance(block, Mapping):
                roles.extend(_strings(block.get("roles")))
    return roles


def _configured_grants(claims: Mapping[str, Any], client_id: object) -> list[str]:
    """Capabilities ``CONNECTOR_PRINCIPAL_GRANTS`` gives the token's principal."""
    names = {str(client_id or "").strip()}
    names.update(name for key in _PRINCIPAL_KEYS for name in _strings(claims.get(key)))
    names.discard("")
    grants: list[str] = []
    for entry in str(setting(PRINCIPAL_GRANTS_SETTING, "") or "").split(";"):
        principal, _, capabilities = entry.partition("=")
        if principal.strip() in names:
            grants.extend(csv_values(capabilities))
    return grants


def token_actor(token: Any) -> ActorContext:
    """The authenticated actor a verified access token names.

    The only place a token becomes an actor: the served security profile and the
    request binding both read it, so they cannot disagree about who is calling.
    """
    claims = token.claims if isinstance(token.claims, Mapping) else {}
    subject = next(
        (claims[key] for key in _SUBJECT_KEYS if _strings(claims.get(key))),
        token.client_id,
    )
    tenant = next(
        (claims[key] for key in _TENANT_KEYS if _strings(claims.get(key))),
        claims.get("iss") or "",
    )
    groups = [
        group.strip("/") for key in _GROUP_KEYS for group in _strings(claims.get(key))
    ]
    return ActorContext(
        actor_id=str(subject).strip(),
        actor_type=ActorType.HUMAN,
        tenant_id=str(tenant).strip(),
        roles=tuple(
            dict.fromkeys(
                [*_roles(claims), *_configured_grants(claims, token.client_id)]
            )
        ),
        groups=tuple(dict.fromkeys(group for group in groups if group)),
        authenticated=True,
    )


def request_actor(*, no_auth: bool = False) -> ActorContext:
    """The actor for the current MCP request, from the credential its transport carries.

    Args:
        no_auth: The operator started the server with auth type ``none``: no
            request carries a credential, so each is the trusted local principal.
    """
    try:
        get_http_request()
    except RuntimeError:
        return SYSTEM_ACTOR
    token = get_access_token()
    if token is None:
        return SYSTEM_ACTOR if no_auth else ANONYMOUS_ACTOR
    return token_actor(token)
