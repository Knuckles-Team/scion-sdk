"""The upstream registry's wire contract (SCION-CONNECTOR-CONTROL-R914).

These constants cross a boundary: the registry (the ``mcp`` extra) writes
them, and certification and the fleet gateway read them without the extra, so
they live here, in the base install, not in ``scion_sdk.mcp``. A connector
with no registry serves no such resource and stamps no such meta; readers
treat absence as "no upstreams".

``connector://upstreams``
    The resource a connector with a registry serves: the live status of every
    declared upstream (name, state, counts, fingerprints). Certification reads
    it to name upstreams in its report; it never calls a registry tool.
``scion/upstream``
    The tool ``meta`` key the registry stamps onto every proxied component,
    holding ``{namespace, upstream}``. A tool that carries the key is owned by
    its upstream release and is excluded from tool pins.
"""

from __future__ import annotations

__all__ = [
    "UPSTREAMS_RESOURCE_URI",
    "UPSTREAM_META_KEY",
    "UPSTREAM_META_NAMESPACE_KEY",
]

#: The served resource that reports every declared upstream's live status.
UPSTREAMS_RESOURCE_URI = "connector://upstreams"
#: The tool ``meta`` key marking a component as proxied from an upstream.
UPSTREAM_META_KEY = "scion/upstream"
#: The ``namespace`` field inside the :data:`UPSTREAM_META_KEY` value.
UPSTREAM_META_NAMESPACE_KEY = "namespace"
