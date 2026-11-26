"""A write-back sink executes only a change set the engine released.

The engine's ``DecisionLog`` ``Writeback`` ``Release`` answers for one approval
queue of the caller's tenant. It releases only a queue whose proposals hold an
approval of a principal other than the queuer, and it appends one audit row per
proposal. :func:`fetch_release` is the only constructor of
:class:`ReleasedChangeSet`; ``run_writeback`` accepts nothing else as approval
(SCION-GOVERNED-WRITEBACK-R006).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from scion_sdk.decide.transport import DecideTransport, DecideUnavailable

__all__ = [
    "RELEASE_ID_MISSING",
    "RELEASE_NOT_FOUND",
    "RELEASE_UNAPPROVED",
    "RELEASE_UNAVAILABLE",
    "ReleaseError",
    "ReleasedChangeSet",
    "fetch_release",
    "release_audit",
]

RELEASE_ID_MISSING: Final = "RELEASE_ID_MISSING"
RELEASE_NOT_FOUND: Final = "RELEASE_NOT_FOUND"
RELEASE_UNAPPROVED: Final = "RELEASE_UNAPPROVED"
RELEASE_UNAVAILABLE: Final = "RELEASE_UNAVAILABLE"

_PREFIX: Final = "changeset:"
_ENGINE_CODES: Final = {
    "WRITEBACK_QUEUE_UNKNOWN": RELEASE_NOT_FOUND,
    "PROPOSAL_UNAPPROVED": RELEASE_UNAPPROVED,
}
_MINT: Final = object()


class ReleaseError(RuntimeError):
    """The engine did not release the change set; ``code`` says why."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class ReleasedChangeSet:
    """A change set the engine released: its id, proposals and approvals."""

    change_set_id: str
    proposals: tuple[Mapping[str, Any], ...]
    approvals: tuple[Mapping[str, Any], ...]
    _mint: object = None

    def __post_init__(self) -> None:
        if self._mint is not _MINT:
            raise TypeError("a ReleasedChangeSet comes only from fetch_release")


def queue_id(change_set_id: str) -> str:
    """The engine queue id of ``change_set_id`` (the id without ``changeset:``)."""
    if not isinstance(change_set_id, str) or not change_set_id.strip():
        raise ReleaseError(RELEASE_ID_MISSING, "the request names no change_set_id")
    return change_set_id.removeprefix(_PREFIX)


def _refusal(exc: Exception) -> ReleaseError:
    text = str(exc)
    for engine_code, code in _ENGINE_CODES.items():
        if engine_code in text:
            return ReleaseError(code, engine_code)
    return ReleaseError(RELEASE_UNAVAILABLE, type(exc).__name__)


async def fetch_release(
    transport: DecideTransport, change_set_id: str, *, tenant_id: str
) -> ReleasedChangeSet:
    """Ask the engine to release ``change_set_id`` for ``tenant_id``.

    Raises:
        ReleaseError: ``RELEASE_ID_MISSING``, ``RELEASE_NOT_FOUND``,
            ``RELEASE_UNAPPROVED`` or ``RELEASE_UNAVAILABLE``.
    """
    queue = queue_id(change_set_id)
    op = {
        "op": "writeback",
        "tenant_id": tenant_id,
        "computation": {"kind": "release", "queue_id": queue},
    }
    try:
        reply = await transport.log(op)
    except (DecideUnavailable, RuntimeError, OSError) as exc:
        raise _refusal(exc) from None
    released = reply.get("change_set") if isinstance(reply, Mapping) else None
    if (
        not isinstance(released, Mapping)
        or reply.get("result") != "released"
        or released.get("change_set_id") != f"{_PREFIX}{queue}"
    ):
        raise ReleaseError(RELEASE_UNAVAILABLE, "the engine returned no release")
    return ReleasedChangeSet(
        change_set_id=str(released["change_set_id"]),
        proposals=tuple(released.get("proposals") or ()),
        approvals=tuple(released.get("approvals") or ()),
        _mint=_MINT,
    )


async def release_audit(
    transport: DecideTransport, change_set_id: str, *, tenant_id: str
) -> tuple[Mapping[str, Any], ...]:
    """The engine's audit rows of a change set the caller released."""
    op = {
        "op": "writeback_audit",
        "tenant_id": tenant_id,
        "change_set_id": f"{_PREFIX}{queue_id(change_set_id)}",
    }
    try:
        reply = await transport.log(op)
    except (DecideUnavailable, RuntimeError, OSError) as exc:
        raise _refusal(exc) from None
    rows = reply.get("rows") if isinstance(reply, Mapping) else None
    return tuple(rows or ())
