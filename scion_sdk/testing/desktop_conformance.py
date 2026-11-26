"""Conformance run for native desktop providers (SCION-CONNECTOR-CONTROL-R060).

``run_desktop_conformance`` reads the cursor and focus before and after every
call and raises ``AssertionError`` when a provider moves either.
"""

from __future__ import annotations

from scion_sdk.ports.native_desktop import (
    DesktopGrant,
    DesktopObserver,
    NativeDesktopProvider,
)

__all__ = ["run_desktop_conformance"]


async def run_desktop_conformance(
    provider: NativeDesktopProvider,
    observer: DesktopObserver,
    grant: DesktopGrant,
    target: str,
) -> None:
    """Drive ``click``, ``type_text`` and ``verify``; each must leave the state as it was."""
    for name, call in (
        ("click", lambda: provider.click(grant, target)),
        ("type_text", lambda: provider.type_text(grant, target, "conformance")),
        ("verify", lambda: provider.verify(grant, target)),
    ):
        before = await observer.state()
        await call()
        if await observer.state() != before:
            raise AssertionError(f"{name} changed cursor or focus")
