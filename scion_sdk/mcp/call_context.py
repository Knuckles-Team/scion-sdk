"""Caller-scoped tool context and ordered progress for every served call.

SCION-CONNECTOR-CONTROL-R050.2-R050.5. :class:`ToolContextMiddleware` runs after
the actor binding and builds one :class:`ToolExecutionContext` per ``tools/call``
from the bound actor, the server name and the tool name. Each call owns one
:class:`OrderedProgress` and one :class:`RequestCache`. A call refuses before
any backend effect on a blank identity, a destructive operation without an
approval reference, or a repeated request id. Each call writes one
``tool_call_receipt`` and carries it in ``_meta["scion/receipt"]``; the receipt
holds no cached value, credential or raw argument. Arbora verifies approvals
and stores receipts; the SDK keeps no durable store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.shared.exceptions import MCPError

from scion_sdk.config import setting
from scion_sdk.identity import current_actor
from scion_sdk.mcp.context import (
    ToolExecutionContext,
    ToolExecutionResult,
    ToolProgress,
    ctx_log,
)
from scion_sdk.mcp.progress import OrderedProgress
from scion_sdk.mcp.request_cache import RequestCache

__all__ = [
    "APPROVAL_META_KEY",
    "RECEIPT_META_KEY",
    "REFUSAL_CODES",
    "ToolContextMiddleware",
    "ToolRefusal",
    "current_tool_context",
    "current_tool_progress",
    "record_effect",
]

APPROVAL_META_KEY = "arbora/approval_ref"
RECEIPT_META_KEY = "scion/receipt"
REFUSAL_CODES = frozenset(
    {
        "invalid_context",
        "invalid_progress",
        "approval_required",
        "deadline_exceeded",
        "cancelled",
        "delivery_failed",
        "recovery_unavailable",
    }
)
_LEDGER_ENTRIES = 1024
_REFUSAL_RPC_CODE = -32000
_logger = logging.getLogger(__name__)


class ToolRefusal(Exception):
    """A call was refused; ``code`` is one of :data:`REFUSAL_CODES`."""

    def __init__(self, code: str) -> None:
        if code not in REFUSAL_CODES:
            raise ValueError("unknown refusal code")
        super().__init__(code)
        self.code = code

    def to_error(self) -> MCPError:
        return MCPError(_REFUSAL_RPC_CODE, self.code, {"reason": self.code})


@dataclass
class _CallState:
    context: ToolExecutionContext
    progress: OrderedProgress
    cache: RequestCache[Any]
    effect_receipt_ref: str | None = None
    effect_started: bool = False
    counters: dict[str, int] = field(
        default_factory=lambda: {"cache_hits": 0, "notification_loss": 0}
    )


_state: ContextVar[_CallState | None] = ContextVar("scion_tool_call", default=None)


def current_tool_context() -> ToolExecutionContext | None:
    """The context of the call being served, or ``None`` outside one."""
    state = _state.get()
    return state.context if state else None


def current_tool_progress() -> OrderedProgress | None:
    """The ordered progress of the call being served."""
    state = _state.get()
    return state.progress if state else None


def record_effect(receipt_ref: str | None = None) -> None:
    """Mark that a source effect may have happened; optionally its receipt reference."""
    state = _state.get()
    if state is not None:
        state.effect_started = True
        if receipt_ref:
            state.effect_receipt_ref = receipt_ref


def _deadline_seconds() -> int:
    value = setting("SCION_TOOL_DEADLINE_SECONDS", 300, cast=int)
    return value if isinstance(value, int) and 1 <= value <= 3600 else 300


class ToolContextMiddleware(Middleware):
    """Build and bind one execution context per ``tools/call``."""

    def __init__(
        self,
        connector_id: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connector = connector_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ledger: OrderedDict[str, str] = OrderedDict()

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: Any
    ) -> Any:
        fast_ctx = context.fastmcp_context
        tool = str(getattr(context.message, "name", ""))
        meta = _message_meta(fast_ctx)
        try:
            execution = self._execution(fast_ctx, tool, meta)
            await self._admit(fast_ctx, tool, execution, meta)
        except ToolRefusal as refusal:
            raise refusal.to_error() from None
        state = self._state_for(execution, fast_ctx)
        token = _state.set(state)
        reason, outcome = "succeeded", "succeeded"
        try:
            result = await call_next(context)
        except ToolRefusal as refusal:
            reason = outcome = refusal.code
            raise refusal.to_error() from None
        except TimeoutError:
            reason = outcome = "deadline_exceeded"
            raise ToolRefusal("deadline_exceeded").to_error() from None
        except BaseException:
            reason = "failed"
            outcome = "uncertain" if state.effect_started else "failed"
            raise
        finally:
            _state.reset(token)
            self._remember(execution.request_id, outcome)
            await self._receipt(fast_ctx, state, tool, reason, outcome, meta)
        return _with_receipt(
            result, state, tool, reason, outcome, meta, self._connector
        )

    def _execution(
        self, fast_ctx: Any, tool: str, meta: dict[str, Any]
    ) -> ToolExecutionContext:
        actor = current_actor()
        request_id = str(getattr(fast_ctx, "request_id", "") or uuid.uuid4().hex)
        session = _session(fast_ctx)
        token = meta.get("progressToken")
        try:
            return ToolExecutionContext(
                tenant_id=actor.tenant_id,
                actor_id=actor.actor_id,
                session_id=session,
                request_id=request_id,
                connector_id=self._connector,
                operation_id=tool,
                deadline=self._clock() + timedelta(seconds=_deadline_seconds()),
                progress_token=token if isinstance(token, str | int) else None,
            )
        except ValueError:
            raise ToolRefusal("invalid_context") from None

    async def _admit(
        self,
        fast_ctx: Any,
        tool: str,
        execution: ToolExecutionContext,
        meta: dict[str, Any],
    ) -> None:
        if execution.request_id in self._ledger:
            raise ToolRefusal("recovery_unavailable")
        if await _is_destructive(fast_ctx, tool) and not meta.get(APPROVAL_META_KEY):
            raise ToolRefusal("approval_required")

    def _state_for(self, execution: ToolExecutionContext, fast_ctx: Any) -> _CallState:
        counters = {"cache_hits": 0, "notification_loss": 0}

        async def send(token: str | int, progress: ToolProgress) -> None:
            try:
                await fast_ctx.report_progress(
                    progress=progress.completed,
                    total=progress.total,
                    message=progress.message,
                )
            except Exception:
                counters["notification_loss"] += 1
                raise ToolRefusal("delivery_failed") from None

        state = _CallState(
            context=execution,
            progress=OrderedProgress(execution, send, lambda text: text),
            cache=RequestCache(execution, lambda value: True),
        )
        state.counters = counters
        return state

    def _remember(self, request_id: str, outcome: str) -> None:
        self._ledger[request_id] = outcome
        self._ledger.move_to_end(request_id)
        while len(self._ledger) > _LEDGER_ENTRIES:
            self._ledger.popitem(last=False)

    async def _receipt(
        self,
        fast_ctx: Any,
        state: _CallState,
        tool: str,
        reason: str,
        outcome: str,
        meta: dict[str, Any],
    ) -> None:
        body = _receipt_body(state, tool, reason, outcome, meta, self._connector)
        await ctx_log(
            fast_ctx,
            json.dumps({"event": "tool_call_receipt", **body}, sort_keys=True),
            logger=_logger,
        )


def _message_meta(fast_ctx: Any) -> dict[str, Any]:
    raw = getattr(getattr(fast_ctx, "request_context", None), "meta", None)
    if raw is None:
        return {}
    dump = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    return {k: v for k, v in dump.items() if v is not None}


def _session(fast_ctx: Any) -> str:
    try:
        return str(fast_ctx.session_id)
    except Exception:
        return "local"


async def _is_destructive(fast_ctx: Any, tool: str) -> bool:
    try:
        found = await fast_ctx.fastmcp.get_tool(tool)
    except Exception:
        return False
    annotations = getattr(found, "annotations", None)
    return bool(getattr(annotations, "destructive_hint", False))


def _receipt_body(
    state: _CallState,
    tool: str,
    reason: str,
    outcome: str,
    meta: dict[str, Any],
    connector: str,
) -> dict[str, Any]:
    execution = state.context
    last = state.progress.last
    result = ToolExecutionResult(
        state=outcome
        if outcome in {"succeeded", "failed", "cancelled", "uncertain"}
        else "failed",  # type: ignore[arg-type]
        last_sequence=last.sequence if last else 0,
        effect_receipt_ref=state.effect_receipt_ref,
    )
    return {
        "tenant": execution.tenant_id,
        "actor": execution.actor_id,
        "connector": connector,
        "operation": tool,
        "request_id": execution.request_id,
        "trace_id": execution.request_id,
        "schema_digest": hashlib.sha256(tool.encode()).hexdigest(),
        "last_sequence": result.last_sequence,
        "terminal_reason": reason,
        "state": result.state,
        "approval_ref": meta.get(APPROVAL_META_KEY),
        "effect_receipt_ref": result.effect_receipt_ref,
        "counters": dict(state.counters),
    }


def _with_receipt(
    result: Any,
    state: _CallState,
    tool: str,
    reason: str,
    outcome: str,
    meta: dict[str, Any],
    connector: str,
) -> Any:
    body = _receipt_body(state, tool, reason, outcome, meta, connector)
    existing = dict(getattr(result, "meta", None) or {})
    result.meta = {**existing, RECEIPT_META_KEY: body}
    return result
