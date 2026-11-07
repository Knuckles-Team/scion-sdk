"""The SDK's own consumers of EG's ``Decide`` layer (SCION-CONNECTOR-CONTROL-R006,
SCION-GOVERNED-WRITEBACK-R001).

The SDK is the Medulla client adapter for decisions: the transport, the runner
that calls ``Decide`` and the outcome reading. The decision-point catalog, the
candidate heuristics and the choice of an option are Medulla's. A call site
passes its deterministic rule as the received fallback::

    from scion_sdk import decide
    choice = decide.choose("connector.tool", options, fallback=lambda: picked)

With no runner installed, or a point Medulla has not published, :func:`choose`
is exactly the fallback. A process that also holds a verified Medulla session
may :func:`install_runner` a real
:class:`~scion_sdk.ports.decide_runner.DecisionRunner`;
:class:`~scion_sdk.decide.medulla.MedullaDecisionRunner` is installed at the
connector-sync composition root
(:func:`scion_sdk.runner.composition.default_services`) when the process was
given a tenant to decide as; tests install fakes. The call shapes are in
:mod:`scion_sdk.decide.consumers`.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any

from scion_sdk.decide.options import (
    Option,
    declared_source,
    q32,
    text_param,
    unique_sorted,
)
from scion_sdk.decide.outcome import Choice
from scion_sdk.ports.decide_runner import DecisionRunner, Fallback

_RUNNER: ContextVar[DecisionRunner | None] = ContextVar(
    "scion_sdk_decide_runner", default=None
)
_PROCESS: list[DecisionRunner | None] = [None]


def install_runner(runner: DecisionRunner | None) -> None:
    """Install ``runner`` for the whole process (``None`` uninstalls)."""
    _PROCESS[0] = runner


def use_runner(runner: DecisionRunner | None) -> Any:
    """Scope ``runner`` to the current context; returns the reset token."""
    return _RUNNER.set(runner)


def current_runner() -> DecisionRunner | None:
    """The context's runner, else the process runner, else ``None``."""
    return _RUNNER.get() or _PROCESS[0]


def _no_runner(fallback: Fallback) -> Choice:
    return Choice(fallback(), False, "no_runner")


#: The rest of a ``choose``/``achoose`` call (``params``, ``candidates``): kept
#: as ``**kwargs`` here, forwarded verbatim to a :class:`DecisionRunner`, so
#: this dispatcher's signature does not restate
#: :class:`~scion_sdk.ports.decide_runner.DecisionRunner`'s own
#: canonical one (defined once, in ``ports/``) a second time.
def choose(
    question_id: str, options: Sequence[Option], fallback: Fallback, **kwargs: Any
) -> Choice:
    """Decide ``question_id`` from a sync call site."""
    runner = current_runner()
    if runner is None:
        return _no_runner(fallback)
    return runner.choose(question_id, options, fallback, **kwargs)


async def achoose(
    question_id: str, options: Sequence[Option], fallback: Fallback, **kwargs: Any
) -> Choice:
    """Decide ``question_id`` from an async call site."""
    runner = current_runner()
    if runner is None:
        return _no_runner(fallback)
    return await runner.achoose(question_id, options, fallback, **kwargs)


__all__ = [
    "Choice",
    "DecisionRunner",
    "Option",
    "achoose",
    "choose",
    "current_runner",
    "declared_source",
    "install_runner",
    "q32",
    "text_param",
    "unique_sorted",
    "use_runner",
]
