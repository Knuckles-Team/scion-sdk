"""Named upstream instances a connector process serves.

SCION-CENTRALIZED-CAPABILITIES-R050. A connector process may serve
several upstream instances of one system. The deployment names them in
``<PREFIX>_INSTANCES``, a JSON object mapping an instance name to one
object holding ``base_url``, an optional ``route_host``, optional
``access_headers`` (header name to a secret reference) and an optional
``tls_profile_ref`` (a secret reference holding a TLS profile).
``<PREFIX>_DEFAULT_INSTANCE`` names the default instance; without it,
the first entry is the default.

With no map set, the connector's one-URL settings (``<PREFIX>_BASE_URL``,
``<PREFIX>_ROUTE_HOST``, ``<PREFIX>_ACCESS_HEADERS``,
``<PREFIX>_TLS_PROFILE_REF``) name one implicit ``default`` instance,
so a deployment that sets no map is unchanged.

This module stays in the foundation layer: the connector passes its own
client class and, when access headers hold secret references, the
resolver that turns them into values (the served tool surface applies
the per-instance caller entitlement check; :attr:`ConnectorInstances.
namespace` names the resource namespace it checks against).
:meth:`ConnectorInstances.client_for` builds the client from the entry.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from scion_sdk.config import ConfigurationError, setting

__all__ = ["ConnectorInstances", "InstanceEntry", "UnknownInstanceError"]

#: Name of the single implicit instance when no map is configured.
IMPLICIT_INSTANCE = "default"

_ENTRY_KEYS = frozenset({"base_url", "route_host", "access_headers", "tls_profile_ref"})


class UnknownInstanceError(ConfigurationError):
    """The requested instance name is not served by this connector.

    The message lists the instance names only -- never entry values such
    as URLs or secret references.
    """

    def __init__(self, name: str, names: Sequence[str]) -> None:
        super().__init__(
            f"unknown instance {name!r}; served instances: {', '.join(names)}"
        )
        self.name = name
        self.known = tuple(names)


@dataclass(frozen=True)
class InstanceEntry:
    """One configured upstream instance.

    ``access_headers`` holds the configured values verbatim -- a secret
    reference (``env://``/``openbao://``) or a literal -- and is resolved
    when a client is built.
    """

    base_url: str
    route_host: str | None = None
    access_headers: Mapping[str, str] = field(default_factory=dict)
    tls_profile_ref: str | None = None


def _string_map(value: Any) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(name, str) and isinstance(item, str) for name, item in value.items()
    )


def _parse_entry(name: str, value: Any) -> InstanceEntry:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"instance {name!r} must be a JSON object")
    unknown = {str(key) for key in value} - _ENTRY_KEYS
    if unknown:
        raise ConfigurationError(
            f"instance {name!r} holds unknown keys: {', '.join(sorted(unknown))}"
        )
    base_url = value.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise ConfigurationError(f"instance {name!r} needs a base_url")
    route_host = value.get("route_host")
    if route_host is not None and not isinstance(route_host, str):
        raise ConfigurationError(f"instance {name!r} route_host must be a string")
    headers = value.get("access_headers")
    if headers is not None and not _string_map(headers):
        raise ConfigurationError(
            f"instance {name!r} access_headers must map header names to strings"
        )
    tls_profile_ref = value.get("tls_profile_ref")
    if tls_profile_ref is not None and not isinstance(tls_profile_ref, str):
        raise ConfigurationError(f"instance {name!r} tls_profile_ref must be a string")
    return InstanceEntry(
        base_url=base_url,
        route_host=route_host,
        access_headers=dict(headers or {}),
        tls_profile_ref=tls_profile_ref,
    )


class ConnectorInstances:
    """The named upstream instances of one connector process.

    Reads ``<PREFIX>_INSTANCES`` and ``<PREFIX>_DEFAULT_INSTANCE`` at
    construction (the prefix is the settings prefix, conventionally
    uppercase); with no map, the one-URL settings name the implicit
    :data:`IMPLICIT_INSTANCE` instance, so a deployment that sets no map
    is unchanged.

    Args:
        prefix: The connector's settings prefix: ``"crm"`` and ``"CRM"``
            both read ``CRM_INSTANCES``.
        client_cls: The connector's own API client class. ``client_for``
            builds the instance client from it, so each connector keeps
            its own client class.
        resolver: Turns a configured access-header value -- a secret
            reference (``env://``/``openbao://``) or a literal -- into the
            value a client sends. Required when any entry holds
            ``access_headers``; wire it to the connector's own secret
            resolution (the environment plus OpenBao when configured).
        namespace: Resource namespace of the per-instance entitlement
            check; defaults to the lowercased prefix.

    Raises:
        ConfigurationError: the map is not a JSON object, an entry is
            malformed, the named default is not served, or access headers
            are configured without a resolver.
    """

    def __init__(
        self,
        prefix: str,
        *,
        client_cls: type,
        resolver: Callable[[str], str] | None = None,
        namespace: str | None = None,
    ) -> None:
        if not prefix:
            raise ConfigurationError("the settings prefix must not be empty")
        self._prefix = prefix
        self._client_cls = client_cls
        self._namespace = namespace or prefix.lower()
        self._resolver = resolver
        raw = setting(f"{prefix.upper()}_INSTANCES", "")
        if not raw:
            self._entries: dict[str, InstanceEntry] | None = None
            self._implicit: InstanceEntry | None = self._implicit_entry()
            self._default = IMPLICIT_INSTANCE
        else:
            try:
                mapping = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ConfigurationError(
                    f"{prefix.upper()}_INSTANCES must be a JSON object"
                ) from exc
            if not isinstance(mapping, Mapping) or not all(
                isinstance(name, str) for name in mapping
            ):
                raise ConfigurationError(
                    f"{prefix.upper()}_INSTANCES must map instance names to objects"
                )
            self._entries = {}
            for name, value in mapping.items():
                if not name.strip():
                    raise ConfigurationError(
                        f"{prefix.upper()}_INSTANCES holds an empty instance name"
                    )
                self._entries[name] = _parse_entry(name, value)
            if not self._entries:
                raise ConfigurationError(
                    f"{prefix.upper()}_INSTANCES must name at least one instance"
                )
            self._implicit = None
            named = setting(f"{prefix.upper()}_DEFAULT_INSTANCE", "")
            if named:
                if named not in self._entries:
                    raise UnknownInstanceError(named, self.names)
                self._default = named
            else:
                self._default = next(iter(self._entries))
        if self._needs_resolver():
            if resolver is None:
                raise ConfigurationError(
                    f"{prefix.upper()}_INSTANCES access_headers hold secret "
                    "references: pass a resolver to ConnectorInstances"
                )
            if not callable(resolver):
                raise ConfigurationError("the resolver must be callable")

    @property
    def namespace(self) -> str:
        """The resource namespace of the per-instance entitlement check."""
        return self._namespace

    @property
    def names(self) -> tuple[str, ...]:
        """The served instance names; the implicit name without a map."""
        if self._entries is not None:
            return tuple(self._entries)
        return (IMPLICIT_INSTANCE,)

    @property
    def default(self) -> str:
        """The default instance name: the named one, else the first entry."""
        return self._default

    def entry(self, name: str | None = None) -> InstanceEntry:
        """The entry for ``name``, or the default's when ``name`` is ``None``.

        Raises:
            UnknownInstanceError: the name is not a served instance.
        """
        resolved = self.default if name is None else name
        if self._entries is not None:
            try:
                return self._entries[resolved]
            except KeyError:
                raise UnknownInstanceError(resolved, self.names) from None
        implicit = self._implicit
        if implicit is None:
            raise UnknownInstanceError(resolved, self.names)
        return implicit

    def client_for(self, name: str | None = None) -> Any:
        """Build the connector's client for one instance from its entry.

        Access-header values are resolved through the injected resolver
        (when configured); a named instance the caller may not reach is
        refused by the served tool surface before this builds anything.

        Raises:
            UnknownInstanceError: ``name`` is not a served instance.
            ConfigurationError: no upstream is configured at all.
        """
        instance_name = self.default if name is None else name
        entry = self.entry(instance_name)
        if not entry.base_url:
            prefix = self._prefix.upper()
            raise ConfigurationError(
                f"no upstream configured: set {prefix}_INSTANCES or {prefix}_BASE_URL"
            )
        kwargs: dict[str, Any] = {}
        if entry.route_host is not None:
            kwargs["route_host"] = entry.route_host
        if entry.access_headers:
            kwargs["headers"] = self._resolved_headers(entry.access_headers)
        if entry.tls_profile_ref is not None:
            kwargs["tls_profile_ref"] = entry.tls_profile_ref
        return self._client_cls(base_url=entry.base_url, **kwargs)

    def _needs_resolver(self) -> bool:
        if self._entries is None:
            implicit = self._implicit
            return bool(implicit is not None and implicit.access_headers)
        return any(entry.access_headers for entry in self._entries.values())

    def _resolved_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        resolver = self._resolver
        resolved: dict[str, str] = {}
        for name, value in headers.items():
            resolved[name] = resolver(value) if resolver is not None else value
        return resolved

    def _implicit_entry(self) -> InstanceEntry:
        prefix = self._prefix.upper()
        raw_headers = setting(f"{prefix}_ACCESS_HEADERS", "")
        headers: dict[str, str] = {}
        if raw_headers:
            try:
                mapping = json.loads(raw_headers)
            except json.JSONDecodeError as exc:
                raise ConfigurationError(
                    f"{prefix}_ACCESS_HEADERS must be a JSON object"
                ) from exc
            if not _string_map(mapping):
                raise ConfigurationError(
                    f"{prefix}_ACCESS_HEADERS must map header names to string values"
                )
            headers = dict(mapping)
        return InstanceEntry(
            base_url=setting(f"{prefix}_BASE_URL", ""),
            route_host=setting(f"{prefix}_ROUTE_HOST", "") or None,
            access_headers=headers,
            tls_profile_ref=setting(f"{prefix}_TLS_PROFILE_REF", "") or None,
        )
