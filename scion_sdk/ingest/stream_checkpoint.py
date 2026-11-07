"""Validate host-authorized generation checkpoints for unpaged source streams."""

from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["authorize_generation"]


async def authorize_generation(
    authorize: Callable[[str, Any], Awaitable[bool]],
    *,
    tenant: str,
    context: Any,
    mode: str,
    state: dict[str, Any] | None,
    binding: str,
) -> None:
    """Refuse a non-delta or unauthorized read, or a checkpoint not bound to ``binding``."""
    if mode != "delta" or not await authorize(tenant, context):
        raise ValueError("access refused")
    if state is not None and (
        set(state) != {"binding", "generation"}
        or state["binding"] != binding
        or not isinstance(state["generation"], str)
    ):
        raise ValueError("invalid source checkpoint")
