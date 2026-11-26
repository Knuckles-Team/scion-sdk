"""The native desktop port honours its grant and leaves focus alone (R060)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scion_sdk.ports.native_desktop import (
    REFUSAL_CODES,
    DesktopEvidence,
    DesktopGrant,
    DesktopObserver,
    DesktopRefused,
    GuardedDesktop,
    NativeDesktopProvider,
)
from scion_sdk.testing.desktop_conformance import run_desktop_conformance

NOW = datetime(2026, 11, 26, tzinfo=UTC)


class _Desktop:
    """A fake provider and observer; ``steals_focus`` makes it misbehave."""

    def __init__(self, steals_focus: bool = False) -> None:
        self.steals_focus = steals_focus
        self.focus = "window-a;cursor-1,1"
        self.calls: list[str] = []

    async def state(self) -> str:
        return self.focus

    async def _act(self, name: str) -> None:
        self.calls.append(name)
        if self.steals_focus:
            self.focus = "window-b;cursor-9,9"

    async def click(self, grant: DesktopGrant, target: str) -> None:
        await self._act("click")

    async def type_text(self, grant: DesktopGrant, target: str, text: str) -> None:
        await self._act("type_text")

    async def verify(self, grant: DesktopGrant, target: str) -> bool:
        await self._act("verify")
        return True


def _grant(**changes: object) -> DesktopGrant:
    fields = {
        "tenant_id": "t1",
        "sandbox_id": "s1",
        "scope": "desktop:act",
        "expiry": NOW + timedelta(minutes=5),
    }
    return DesktopGrant(**{**fields, **changes})  # type: ignore[arg-type]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R060")
async def test_provider_honours_grant_and_leaves_focus() -> None:
    desktop = _Desktop()
    assert isinstance(desktop, NativeDesktopProvider) and isinstance(
        desktop, DesktopObserver
    )
    await run_desktop_conformance(desktop, desktop, _grant(), "button")
    guarded = GuardedDesktop(
        desktop, desktop, tenant_id="t1", scope="desktop:act", clock=lambda: NOW
    )
    evidence = await guarded.call("click", _grant(), "button")
    assert isinstance(evidence, DesktopEvidence)
    assert evidence.grant_digest == _grant().digest()
    assert evidence.focus_before == evidence.focus_after
    desktop.calls.clear()
    cases = {
        "grant_missing": None,
        "tenant_mismatch": _grant(tenant_id="t2"),
        "grant_scope_mismatch": _grant(scope="other"),
        "grant_expired": _grant(expiry=NOW - timedelta(seconds=1)),
    }
    for code, grant in cases.items():
        with pytest.raises(DesktopRefused) as caught:
            await guarded.call("type_text", grant, "field", "x")
        assert caught.value.code == code
    assert desktop.calls == []  # refused before any provider effect
    thief = _Desktop(steals_focus=True)
    with pytest.raises(AssertionError, match="changed cursor or focus"):
        await run_desktop_conformance(thief, thief, _grant(), "button")
    thief = _Desktop(steals_focus=True)
    with pytest.raises(DesktopRefused) as caught:
        await GuardedDesktop(
            thief, thief, tenant_id="t1", scope="desktop:act", clock=lambda: NOW
        ).call("click", _grant(), "button")
    assert caught.value.code == "focus_changed" and "focus_changed" in REFUSAL_CODES
