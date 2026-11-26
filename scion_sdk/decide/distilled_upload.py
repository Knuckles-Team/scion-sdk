"""Upload offline teacher scores to the decision log (SCION-CONNECTOR-CONTROL-R905).

The SDK computes no score. A connector hands over the teacher judgments it
produced offline; this module validates them, splits them into engine-sized
batches and sends each as one decision log ``Learn`` write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from scion_sdk.decide.transport import DecideTransport

__all__ = [
    "DEFAULT_MAX_SCORES_PER_WRITE",
    "DISTILLED_SCORE_INVALID",
    "DistilledScoreInvalid",
    "DistilledScoreUploader",
    "TeacherJudgment",
]

DISTILLED_SCORE_INVALID = "DISTILLED_SCORE_INVALID"
#: Applies only when the engine publishes no ``distilled.max_scores_per_write``.
DEFAULT_MAX_SCORES_PER_WRITE = 4096
_Q16_ONE = 65_536


class DistilledScoreInvalid(ValueError):
    """A judgment is outside the Q16 range or its fidelity bounds are inverted."""

    code = DISTILLED_SCORE_INVALID


@dataclass(frozen=True, slots=True)
class TeacherJudgment:
    """One teacher judgment of one document; every number is Q16 in [0, 65536]."""

    document_id: str
    score_q16: int
    fidelity_lo_q16: int
    fidelity_hi_q16: int

    def __post_init__(self) -> None:
        numbers = (self.score_q16, self.fidelity_lo_q16, self.fidelity_hi_q16)
        if not self.document_id or any(
            not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= _Q16_ONE
            for n in numbers
        ):
            raise DistilledScoreInvalid(
                f"{DISTILLED_SCORE_INVALID}: {self.document_id}"
            )
        if self.fidelity_lo_q16 > self.fidelity_hi_q16:
            raise DistilledScoreInvalid(
                f"{DISTILLED_SCORE_INVALID}: fidelity low above high for {self.document_id}"
            )

    def wire(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "score_q16": self.score_q16,
            "fidelity_lo_q16": self.fidelity_lo_q16,
            "fidelity_hi_q16": self.fidelity_hi_q16,
        }


def write_op(
    tenant: str, scorer_id: str, scorer_revision: int, batch: Sequence[TeacherJudgment]
) -> dict[str, Any]:
    """The decision log ``Learn`` op carrying ``UploadDistilledScores``."""
    return {
        "op": "learn",
        "tenant_id": tenant,
        "write": {
            "write": "upload_distilled_scores",
            "upload": {
                "scorer_id": scorer_id,
                "scorer_revision": scorer_revision,
                "scores": [j.wire() for j in batch],
            },
        },
    }


@dataclass(frozen=True, slots=True)
class DistilledScoreUploader:
    """Send teacher judgments to the engine in bounded, idempotent batches."""

    transport: DecideTransport
    tenant: str
    max_scores_per_write: int | None = None

    @property
    def batch_size(self) -> int:
        return self.max_scores_per_write or DEFAULT_MAX_SCORES_PER_WRITE

    async def upload(
        self,
        scorer_id: str,
        scorer_revision: int,
        judgments: Sequence[TeacherJudgment | Mapping[str, Any]],
    ) -> int:
        """Upload every judgment; returns the number of batches sent.

        Judgments are validated before the first send. Engine refusals
        (``IDEMPOTENCY_CONFLICT``, ``SCORER_REVISION_STALE``) surface
        unchanged; a retry resends the same batches, which the engine accepts
        when a repeated document carries an equal score.
        """
        checked = [
            j if isinstance(j, TeacherJudgment) else TeacherJudgment(**j)
            for j in judgments
        ]
        size = self.batch_size
        batches = [checked[i : i + size] for i in range(0, len(checked), size)]
        for batch in batches:
            await self.transport.log(
                write_op(self.tenant, scorer_id, scorer_revision, batch)
            )
        return len(batches)
