"""The one destructive-operation declaration and guard for every connector.

A connector never invents its own flag. It declares which operations are
destructive, once, and the SDK does the rest:

* the declaration becomes the MCP ``destructiveHint`` annotation, so the door
  that serves the connector (the gateway) classifies the call and asks a human
  to approve it;
* the SDK refuses the call unless the live caller confirmed it. That is an MCP
  confirmation answered by the gateway from an approved plan, the standard
  per-call ``confirm`` argument for clients that cannot be asked, or the one
  operator setting :data:`ALLOW_DESTRUCTIVE_SETTING` for unattended use.

A client method served as one tool declares itself with
:func:`destructive_operation`. A hand-written action-routed tool declares its destructive actions with
:func:`destructive_actions`. Operations the SDK derives from a client class are
classified by :func:`scion_sdk.mcp.action_dispatch.is_destructive_action` and
need no declaration.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from mcp.types import InputRequiredResult

from scion_sdk.config import setting
from scion_sdk.mcp.confirmation import confirmation_refusal, ctx_confirm_destructive

__all__ = [
    "ALLOW_DESTRUCTIVE_SETTING",
    "CONFIRM_PARAMETER",
    "declared_destructive_actions",
    "destructive_actions",
    "destructive_operation",
    "guard_destructive",
    "is_declared_destructive",
    "operator_allows_destructive",
]

#: The one operator setting. Unset or false means a destructive call needs a
#: live confirmation; true lets unattended operation proceed.
ALLOW_DESTRUCTIVE_SETTING = "SCION_ALLOW_DESTRUCTIVE"
#: The one per-call argument, for clients that cannot be asked to confirm.
CONFIRM_PARAMETER = "confirm"
_DECLARATION = "__scion_destructive_actions__"
_OPERATION_DECLARATION = "__scion_destructive__"


def operator_allows_destructive() -> bool:
    """True when the operator allowed unattended destructive operation."""
    return bool(setting(ALLOW_DESTRUCTIVE_SETTING, False))


def declared_destructive_actions(fn: Callable[..., Any]) -> frozenset[str]:
    """Actions a tool function declared destructive (empty when none)."""
    return frozenset(getattr(fn, _DECLARATION, ()))


async def guard_destructive(
    ctx: Any,
    operation: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    confirm: bool = False,
) -> Any | None:
    """``None`` when the call may proceed, else the result to return instead."""
    if operator_allows_destructive():
        return None
    confirmation = await ctx_confirm_destructive(
        ctx, operation, confirmation_arguments=arguments, explicit_confirm=confirm
    )
    if isinstance(confirmation, InputRequiredResult):
        return confirmation
    if confirmation is not True:
        return confirmation_refusal(ctx, operation, parameter=CONFIRM_PARAMETER)
    return None


def destructive_operation(method: Callable[..., Any]) -> Callable[..., Any]:
    """Declare a client method destructive; its served tool then asks first.

    Use it for an operation whose name or HTTP verb the SDK would not
    classify (for example ``suspend_user`` or a POST that revokes access).
    """
    setattr(method, _OPERATION_DECLARATION, True)
    return method


def is_declared_destructive(method: Callable[..., Any]) -> bool:
    """True when ``method`` carries :func:`destructive_operation`."""
    return getattr(method, _OPERATION_DECLARATION, False) is True


def destructive_actions(
    *actions: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare the destructive actions of an action-routed tool function.

    The tool function takes ``action``, optional ``ctx`` (the MCP context) and
    optional ``confirm`` arguments. A call naming a declared action runs
    :func:`guard_destructive` first; every other call passes through.
    """
    declared = frozenset(actions)
    if not declared:
        raise ValueError("declare at least one destructive action")

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs).arguments
            action = bound.get("action")
            if action in declared:
                refused = await guard_destructive(
                    bound.get("ctx"),
                    str(action),
                    {k: v for k, v in bound.items() if k not in {"ctx", "confirm"}},
                    confirm=bound.get(CONFIRM_PARAMETER) is True,
                )
                if refused is not None:
                    return refused
            result = fn(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

        setattr(guarded, _DECLARATION, declared | declared_destructive_actions(fn))
        return guarded

    return decorate
