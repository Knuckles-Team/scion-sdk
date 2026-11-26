"""SCION-CONNECTOR-CONTROL-R905: teacher score upload through the decision log."""

from __future__ import annotations

from typing import Any

import pytest

from scion_sdk.decide.distilled_upload import (
    DEFAULT_MAX_SCORES_PER_WRITE,
    DISTILLED_SCORE_INVALID,
    DistilledScoreInvalid,
    DistilledScoreUploader,
    TeacherJudgment,
)


class Recorder:
    def __init__(self, fail_first: bool = False) -> None:
        self.ops: list[dict[str, Any]] = []
        self.fail_first = fail_first

    async def decide(self, request: Any) -> Any:
        raise AssertionError("not used")

    async def log(self, op: Any) -> Any:
        if self.fail_first:
            self.fail_first = False
            raise ConnectionError("transport down")
        self.ops.append(dict(op))

    def run(self, call: Any) -> Any:
        raise AssertionError("not used")


def _judgments(n: int) -> list[TeacherJudgment]:
    return [TeacherJudgment(f"doc-{i}", i % 65537, 100, 200) for i in range(n)]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R905")
async def test_batches_carry_scorer_identity_and_retry_is_idempotent() -> None:
    from medulla.generated.models import DecisionLogOpLearn

    recorder = Recorder()
    uploader = DistilledScoreUploader(recorder, "tenant")
    sent = await uploader.upload("teacher", 3, _judgments(9000))
    assert sent == 3
    assert [len(o["write"]["upload"]["scores"]) for o in recorder.ops] == [
        4096,
        4096,
        808,
    ]
    assert {
        (o["write"]["upload"]["scorer_id"], o["write"]["upload"]["scorer_revision"])
        for o in recorder.ops
    } == {("teacher", 3)}
    DecisionLogOpLearn.model_validate(recorder.ops[0])

    # A transport failure leaves the caller free to resend the same batches.
    flaky = Recorder(fail_first=True)
    retrying = DistilledScoreUploader(flaky, "tenant")
    with pytest.raises(ConnectionError):
        await retrying.upload("teacher", 3, _judgments(10))
    await retrying.upload("teacher", 3, _judgments(10))
    assert flaky.ops == [
        {
            **recorder.ops[0],
            "write": {
                "write": "upload_distilled_scores",
                "upload": {
                    "scorer_id": "teacher",
                    "scorer_revision": 3,
                    "scores": [j.wire() for j in _judgments(10)],
                },
            },
        }
    ]

    # The engine's own bound applies when it publishes one.
    bounded = Recorder()
    assert (
        await DistilledScoreUploader(bounded, "t", max_scores_per_write=64).upload(
            "s", 1, _judgments(130)
        )
        == 3
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R905")
@pytest.mark.parametrize(
    "bad",
    [(-1, 0, 1), (65537, 0, 1), (5, 9, 3), (5, 0, 65537)],
)
async def test_invalid_judgments_are_refused_before_any_send(
    bad: tuple[int, int, int],
) -> None:
    recorder = Recorder()
    mixed = [
        {
            "document_id": "d",
            "score_q16": bad[0],
            "fidelity_lo_q16": bad[1],
            "fidelity_hi_q16": bad[2],
        }
    ]
    with pytest.raises(DistilledScoreInvalid, match="DISTILLED_SCORE_INVALID"):
        await DistilledScoreUploader(recorder, "t").upload(
            "s", 1, [*_judgments(3), *mixed]
        )
    assert recorder.ops == []


def test_the_default_bound_and_refusal_code_are_published() -> None:
    assert DistilledScoreUploader(Recorder(), "t").batch_size == (
        DEFAULT_MAX_SCORES_PER_WRITE
    )
    assert DistilledScoreInvalid.code == DISTILLED_SCORE_INVALID
