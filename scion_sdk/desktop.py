"""Native desktop action port.

SCION-CONNECTOR-CONTROL-R909. A provider performs click, type and verify on a
granted target without capturing the cursor or the keyboard focus. The
conformance kit runs a provider through :func:`check_provider`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

__all__ = [
    "DesktopAction",
    "DesktopActionKind",
    "DesktopContractError",
    "DesktopGrant",
    "DesktopProvider",
    "DesktopResult",
    "check_provider",
]


class DesktopActionKind(StrEnum):
    CLICK = "click"
    TYPE = "type"
    VERIFY = "verify"


@dataclass(frozen=True)
class DesktopAction:
    """One action on a target of a granted window or control."""

    kind: DesktopActionKind
    target: str
    text: str = ""


@dataclass(frozen=True)
class DesktopGrant:
    """The target scope Arbora granted; an action outside it is refused."""

    targets: tuple[str, ...]


@dataclass(frozen=True)
class DesktopResult:
    """What the provider reports; the capture flags must stay false."""

    ok: bool
    evidence: str = ""
    captured_cursor: bool = False
    captured_focus: bool = False


class DesktopContractError(RuntimeError):
    """The provider broke the port contract; ``code`` is closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class DesktopProvider(Protocol):
    """The native desktop action port."""

    def perform(self, action: DesktopAction, grant: DesktopGrant) -> DesktopResult: ...


def check_provider(provider: DesktopProvider, target: str) -> None:
    """Run click, type and verify; refuse a provider that captures cursor or focus."""
    grant = DesktopGrant(targets=(target,))
    for kind in DesktopActionKind:
        result = provider.perform(
            DesktopAction(kind=kind, target=target, text="x"), grant
        )
        if result.captured_cursor or result.captured_focus:
            raise DesktopContractError("DESKTOP_FOCUS_CAPTURE")
