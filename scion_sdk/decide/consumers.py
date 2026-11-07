"""Call-site API for asking Medulla ``Decide`` with a received fallback
(SCION-CONNECTOR-CONTROL-R006, SCION-GOVERNED-WRITEBACK-R001).

The SDK is the Medulla client adapter. It holds no decision-point catalog,
no scoring, ranking or threshold: which options exist for a question and
which one wins is Medulla's. A caller passes the question id Medulla
publishes, the candidates it is willing to accept, and its own
deterministic fallback (the received default). Anything but an executed,
offered option leaves that fallback answering, so with no runner installed
a call site is exactly its deterministic rule.

Both call shapes are EVALUATE-ONLY and never authorize a side effect: a
proposal still goes through the governed write-back port
(:mod:`scion_sdk.writeback`) before anything is written.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from scion_sdk import decide
from scion_sdk.decide.options import Option, text_param

NO_WRITE = "no_write"


def bounded_choice(
    question_id: str, candidates: Sequence[str], picked: str, *, connector: str
) -> str:
    """Confirm or redirect ``picked`` among ``candidates``, never outside them.

    ``picked`` is the caller's received default and the fallback. A pick
    outside ``candidates`` is a caller bug, not a decision: Medulla is never
    asked about it, and it stays the answer.
    """
    if picked not in candidates:
        return picked
    choice = decide.choose(
        question_id,
        [Option(i) for i in sorted(set(candidates))],
        lambda: picked,
        params=[text_param("connector", connector)],
    )
    return str(choice.option_id or picked)


def propose_writeback(
    question_id: str,
    proposals: Mapping[str, Mapping[str, Any]],
    default: str = NO_WRITE,
) -> tuple[str, Mapping[str, Any] | None]:
    """The write-back to PROPOSE, never executed here.

    ``proposals`` maps a proposal id to its governed write-back change-set
    parameters; ``no_write`` is always an option and ``default`` is the
    fallback. Returns ``(id, params)`` -- ``params`` is ``None`` for
    ``no_write`` -- to hand to :class:`scion_sdk.ports.writeback.WriteBackPort`;
    write-back authorization is unchanged.
    """
    ids = sorted({*proposals, NO_WRITE})
    choice = decide.choose(question_id, [Option(i) for i in ids], lambda: default)
    chosen = str(choice.option_id or default)
    return chosen, proposals.get(chosen)


__all__ = ["NO_WRITE", "bounded_choice", "propose_writeback"]
