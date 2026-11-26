"""Ranked order of equivalent platform backends (SCION-CONNECTOR-CONTROL-R904.1).

Medulla owns path identity, rates and preference. This module only offers the
enabled backends as options for the published ``connector.path_preference``
question and applies the advisory order it gets back. Every refusal to rank
(no runner, unbound point, abstention, timeout) keeps the declared order.
"""

from __future__ import annotations

import hashlib

import anyio

from scion_sdk import decide
from scion_sdk.adapters.platform_models import BackendPolicy, PlatformCapability
from scion_sdk.config import setting
from scion_sdk.decide.options import Option

__all__ = [
    "PATH_PREFERENCE_QUESTION",
    "ordered_policies",
    "path_id",
    "path_order_timeout_s",
]

PATH_PREFERENCE_QUESTION = "connector.path_preference"
_TIMEOUT_MS_DEFAULT, _TIMEOUT_MS_MIN, _TIMEOUT_MS_MAX = 250, 10, 5000


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def path_id(
    connector: str, platform: str, channel: str, operation: str, backend_id: str
) -> str:
    """The Medulla path id: SHA-256 of connector, method (the backend) and parameters."""
    parameters = _digest(platform, channel, operation)
    return _digest(connector, backend_id, parameters)


def path_order_timeout_s() -> float:
    """``CONNECTOR_PATH_ORDER_TIMEOUT_MS`` in seconds, clamped to [10, 5000] ms."""
    millis = setting("CONNECTOR_PATH_ORDER_TIMEOUT_MS", _TIMEOUT_MS_DEFAULT, int)
    return max(_TIMEOUT_MS_MIN, min(_TIMEOUT_MS_MAX, int(millis))) / 1000


async def ordered_policies(
    connector: str, capability: PlatformCapability
) -> tuple[BackendPolicy, ...]:
    """The capability's backends in ranked order; the declared order when unranked."""
    declared = capability.backends
    runner = decide.current_runner()
    enabled = [b for b in declared if b.enabled]
    if runner is None or len(enabled) < 2:
        return declared
    ids = {
        b.backend_id: path_id(
            connector,
            capability.platform_id,
            capability.channel_id,
            capability.operation,
            b.backend_id,
        )
        for b in enabled
    }
    by_path = {
        pid: b.backend_id for b, pid in ((b, ids[b.backend_id]) for b in enabled)
    }
    first = ids[enabled[0].backend_id]
    try:
        with anyio.fail_after(path_order_timeout_s()):
            choice = await runner.achoose(
                PATH_PREFERENCE_QUESTION,
                [Option(ids[b.backend_id]) for b in enabled],
                lambda: first,
            )
    except Exception:
        return declared
    scores = {
        by_path[pid]: score for pid, score in choice.advisory.items() if pid in by_path
    }
    if choice.decided and choice.option_id in by_path:
        scores[by_path[choice.option_id]] = max(scores.values(), default=0) + 1
    if not scores:
        return declared
    index = {b.backend_id: i for i, b in enumerate(declared)}
    present = sorted(
        (b for b in declared if b.backend_id in scores),
        key=lambda b: (-scores[b.backend_id], index[b.backend_id]),
    )
    absent = [b for b in declared if b.backend_id not in scores]
    return (*present, *absent)
