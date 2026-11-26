"""The native desktop operation port (SCION-CONNECTOR-CONTROL-R060).

A provider acts in the background and leaves the cursor and the focus
unchanged. Every call carries a :class:`DesktopGrant`; a call without a valid
grant is refused before any provider effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

__all__ = [
    "REFUSAL_CODES",
    "DesktopEvidence",
    "DesktopGrant",
    "DesktopObserver",
    "DesktopRefused",
    "GuardedDesktop",
    "NativeDesktopProvider",
]

REFUSAL_CODES = (
    "grant_missing",
    "grant_expired",
    "grant_scope_mismatch",
    "tenant_mismatch",
    "focus_changed",
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DesktopGrant(_Frozen):
    """Authority for desktop calls in one sandbox."""

    tenant_id: str = Field(min_length=1)
    sandbox_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    expiry: AwareDatetime

    def digest(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DesktopRefused(RuntimeError):
    """A call was refused; ``code`` is one of :data:`REFUSAL_CODES`."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DesktopEvidence(_Frozen):
    """What one call did; holds digests and names, never content."""

    grant_digest: str
    operation: str
    target: str
    focus_before: str
    focus_after: str


@runtime_checkable
class NativeDesktopProvider(Protocol):
    """Background desktop actions; none may move the cursor or take focus."""

    async def click(self, grant: DesktopGrant, target: str) -> None: ...

    async def type_text(self, grant: DesktopGrant, target: str, text: str) -> None: ...

    async def verify(self, grant: DesktopGrant, target: str) -> bool: ...


@runtime_checkable
class DesktopObserver(Protocol):
    """Reads the cursor and focus state that a call must leave unchanged."""

    async def state(self) -> str:
        """A stable description of the cursor position and focused window."""
        ...


class GuardedDesktop:
    """Checks the grant before a call and the focus invariant after it."""

    def __init__(
        self,
        provider: NativeDesktopProvider,
        observer: DesktopObserver,
        *,
        tenant_id: str,
        scope: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._provider = provider
        self._observer = observer
        self._tenant = tenant_id
        self._scope = scope
        self._clock = clock

    def _check(self, grant: DesktopGrant | None) -> DesktopGrant:
        if grant is None:
            raise DesktopRefused("grant_missing")
        if grant.tenant_id != self._tenant:
            raise DesktopRefused("tenant_mismatch")
        if grant.scope != self._scope:
            raise DesktopRefused("grant_scope_mismatch")
        now = self._clock()
        if grant.expiry <= now:
            raise DesktopRefused("grant_expired")
        return grant

    async def call(
        self,
        operation: str,
        grant: DesktopGrant | None,
        target: str,
        text: str = "",
    ) -> DesktopEvidence:
        """Run ``click``, ``type_text`` or ``verify`` under ``grant``."""
        checked = self._check(grant)
        before = await self._observer.state()
        if operation == "click":
            await self._provider.click(checked, target)
        elif operation == "type_text":
            await self._provider.type_text(checked, target, text)
        elif operation == "verify":
            await self._provider.verify(checked, target)
        else:
            raise ValueError("unknown desktop operation")
        after = await self._observer.state()
        if after != before:
            raise DesktopRefused("focus_changed")
        return DesktopEvidence(
            grant_digest=checked.digest(),
            operation=operation,
            target=target,
            focus_before=hashlib.sha256(before.encode()).hexdigest(),
            focus_after=hashlib.sha256(after.encode()).hexdigest(),
        )
