"""Hash-idempotent Camunda and ARIS pushes of the ``kg_intelligence`` payload."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..core import WritebackResult

logger = logging.getLogger(__name__)

INTELLIGENCE_KEY = "kg_intelligence"

__all__ = ["INTELLIGENCE_KEY"]


def _is_empty(payload: dict[str, Any]) -> bool:
    return not any(payload.get(k) for k in payload if not k.startswith("_"))


def _hashed(payload: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in payload.items() if not k.startswith("_")}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return {**body, "_hash": digest}


def _stored_hash(variables: Any) -> str | None:
    var = variables.get(INTELLIGENCE_KEY) if isinstance(variables, dict) else None
    value = var.get("value") if isinstance(var, dict) else None
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed.get("_hash") if isinstance(parsed, dict) else None


def _existing_hash_camunda(client: Any, instance_id: str) -> str | None:
    getter = getattr(client, "get_process_instance_variables", None)
    if not callable(getter):
        return None
    try:
        variables = getter(instance_id) or {}
        return _stored_hash(variables)
    except Exception:
        return None


def _fetch_camunda_instances(
    list_instances: Any, process_key: str, result: WritebackResult
) -> list[Any] | None:
    try:
        instances = list_instances({"processDefinitionKey": process_key}) or []
    except Exception as exc:
        logger.debug(
            "camunda list_process_instances failed for %s: %s", process_key, exc
        )
        result.errors += 1
        return None
    if isinstance(instances, dict):
        instances = instances.get("items") or instances.get("results") or []
    return instances


def _push_one_camunda_instance(
    client: Any,
    inst: Any,
    *,
    modify: Any,
    hashed: dict[str, Any],
    value: str,
    result: WritebackResult,
) -> None:
    instance_id = (
        inst.get("id") if isinstance(inst, dict) else getattr(inst, "id", None)
    )
    if not instance_id:
        return
    if _existing_hash_camunda(client, instance_id) == hashed["_hash"]:
        result.skipped += 1
        return
    try:
        modify(
            instance_id,
            {"modifications": {INTELLIGENCE_KEY: {"value": value, "type": "Json"}}},
        )
        result.enriched += 1
    except Exception as exc:
        logger.debug("camunda modify vars failed for %s: %s", instance_id, exc)
        result.errors += 1


def _push_camunda(
    client: Any, process_key: str, *, payload: dict[str, Any], result: WritebackResult
) -> None:
    list_instances = getattr(client, "list_process_instances", None)
    modify = getattr(client, "modify_process_instance_variables", None)
    if not callable(list_instances) or not callable(modify):
        return
    instances = _fetch_camunda_instances(list_instances, process_key, result)
    if instances is None:
        return
    if not instances:
        result.skipped += 1
        return
    hashed = _hashed(payload)
    value = json.dumps(hashed, default=str)
    for inst in instances:
        _push_one_camunda_instance(
            client, inst, modify=modify, hashed=hashed, value=value, result=result
        )


def _hash_of(current: Any) -> str | None:
    parsed = json.loads(current) if isinstance(current, str) else current
    return parsed.get("_hash") if isinstance(parsed, dict) else None


def _aris_hash_matches(getter: Any, model_id: str, hashed: dict[str, Any]) -> bool:
    if not callable(getter):
        return False
    try:
        attrs = getter(model_id) or {}
        current = attrs.get(INTELLIGENCE_KEY) if isinstance(attrs, dict) else None
        return bool(_hash_of(current) == hashed["_hash"])
    except Exception:
        return False


def _push_aris(
    client: Any, model_id: str, *, payload: dict[str, Any], result: WritebackResult
) -> None:
    setter = getattr(client, "set_model_attributes", None)
    if not callable(setter):
        return
    hashed = _hashed(payload)
    getter = getattr(client, "list_model_attributes", None) or getattr(
        client, "get_model", None
    )
    if _aris_hash_matches(getter, model_id, hashed):
        result.skipped += 1
        return
    try:
        setter(model_id, {INTELLIGENCE_KEY: json.dumps(hashed, default=str)})
        result.enriched += 1
    except Exception as exc:
        logger.debug("aris set_model_attributes failed for %s: %s", model_id, exc)
        result.errors += 1
