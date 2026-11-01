"""External-id resolution and engine stamping helpers for write-back."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["resolve_external_id"]


def _upsert_stamp(
    add_node: Callable[..., Any],
    node_id: str,
    *,
    label: str,
    props: dict[str, Any],
) -> bool:
    """Merge-upsert stamp props with either engine ``add_node`` signature."""
    try:
        # IntelligenceGraphEngine.add_node(node_id, node_type, properties) — merge-upsert.
        add_node(node_id, label, props)
        return True
    except TypeError:
        return _upsert_stamp_kwargs(add_node, node_id, label, props)
    except Exception:  # stamping is best-effort
        logger.debug("stamp_external_id failed for %s", node_id, exc_info=True)
        return False


def _upsert_stamp_kwargs(
    add_node: Callable[..., Any], node_id: str, label: str, props: dict[str, Any]
) -> bool:
    try:
        # Alternate signature: add_node(node_id, label="", **properties).
        add_node(node_id, label=label, **props)
        return True
    except Exception:  # stamping is best-effort
        logger.debug("stamp_external_id fallback failed", exc_info=True)
        return False


def _load_external_ids(backend: Any, domain: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if backend is None:
        return mapping
    try:
        rows = backend.execute(
            "MATCH (n) WHERE n.domain = $d AND n.externalToolId IS NOT NULL "
            "RETURN n.id AS id, n.externalToolId AS guid",
            {"d": domain},
        )
    except Exception:
        logger.debug("external-id resolver query failed for %s", domain, exc_info=True)
        return mapping
    for r in rows or []:
        if isinstance(r, dict) and r.get("id") and r.get("guid"):
            mapping[str(r["id"])] = str(r["guid"])
    return mapping


def resolve_external_id(backend: Any, domain: str) -> Callable[[str], str | None]:
    """KG node id → upstream external id, via the ``externalToolId``/``domain`` key.

    The shared federation resolver (generalized from the LeanIX-specific one): one
    query per domain, cached in a closure.
    """
    mapping = _load_external_ids(backend, domain)

    def _resolve(node_id: str) -> str | None:
        return mapping.get(node_id)

    return _resolve
