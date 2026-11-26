"""The native desktop action port (R909)."""

from __future__ import annotations

import pytest

from scion_sdk.desktop import (
    DesktopAction,
    DesktopActionKind,
    DesktopContractError,
    DesktopGrant,
    DesktopProvider,
    DesktopResult,
    check_provider,
)


class _Provider:
    def __init__(self, *, steals: bool = False) -> None:
        self.steals = steals
        self.seen: list[str] = []

    def perform(self, action: DesktopAction, grant: DesktopGrant) -> DesktopResult:
        assert action.target in grant.targets
        self.seen.append(action.kind.value)
        return DesktopResult(ok=True, captured_focus=self.steals)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R909")
def test_provider_never_captures_focus() -> None:
    assert [k.value for k in DesktopActionKind] == ["click", "type", "verify"]
    good = _Provider()
    assert isinstance(good, DesktopProvider)
    check_provider(good, "window:notes")
    assert good.seen == ["click", "type", "verify"]
    with pytest.raises(DesktopContractError) as caught:
        check_provider(_Provider(steals=True), "window:notes")
    assert caught.value.code == "DESKTOP_FOCUS_CAPTURE"
