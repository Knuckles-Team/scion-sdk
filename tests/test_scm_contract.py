"""SCION-SCM-R003.1: the change-proposal records are frozen closed-set records."""

from __future__ import annotations

import typing
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scion_sdk.scm import (
    ChangeProposalRef,
    CheckRunRecord,
    ReviewRecord,
    ReviewThreadRecord,
    ScmFeedbackEvent,
)
from scion_sdk.scm.records import (
    SCM_FEEDBACK_KINDS,
    SCM_REVIEW_STATES,
    SCM_REVIEWER_KINDS,
    FeedbackKind,
    ReviewerKind,
    ReviewState,
)

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
DIGEST = "b" * 64


def _proposal(**changes: object) -> ChangeProposalRef:
    data: dict[str, object] = {
        "provider": "github",
        "repository": "acme/widgets",
        "number": 42,
        "head_sha": "a" * 40,
        "base_ref": "main",
        "state": "open",
        "author": "octo",
        "changed_lines": 120,
    }
    data.update(changes)
    return ChangeProposalRef(**data)


def _review(**changes: object) -> ReviewRecord:
    data: dict[str, object] = {
        "id": "rev-1",
        "proposal": _proposal(),
        "reviewer": "octo",
        "reviewer_kind": "person",
        "state": "approved",
        "commit_sha": "c" * 40,
        "submitted_at": NOW,
        "run_key": "run-7",
    }
    data.update(changes)
    return ReviewRecord(**data)


def _event(**changes: object) -> ScmFeedbackEvent:
    data: dict[str, object] = {
        "event_id": "evt-1",
        "provider": "github",
        "kind": "review_submitted",
        "proposal": _proposal(),
        "actor": "octo",
        "actor_kind": "person",
        "at": NOW,
        "body_digest": DIGEST,
    }
    data.update(changes)
    return ScmFeedbackEvent(**data)


@pytest.mark.spec("SCION-SCM-R003.1")
def test_records_are_frozen_and_closed_sets_refuse_unknown_values() -> None:
    """Every record validates and refuses mutation; each closed set refuses
    an unknown value naming the field."""
    proposal = _proposal()
    review = _review()
    thread = ReviewThreadRecord(
        id="thr-1",
        proposal=proposal,
        path="src/app.py",
        line=10,
        resolved=False,
        comments=("make this typed",),
    )
    check_run = CheckRunRecord(
        id="chk-1",
        proposal=proposal,
        head_sha="a" * 40,
        name="mypy",
        status="completed",
        conclusion="success",
        url="https://ci.example.test/runs/7",
    )
    event = _event()
    for record in (proposal, review, thread, check_run, event):
        with pytest.raises(ValidationError, match="frozen"):
            record.__setattr__("provider", "gitlab")  # type: ignore[attr-defined]

    assert SCM_REVIEWER_KINDS == ("person", "automated")
    assert SCM_REVIEW_STATES == (
        "approved",
        "changes_requested",
        "commented",
        "dismissed",
    )
    assert SCM_FEEDBACK_KINDS == (
        "check_changed",
        "review_submitted",
        "review_comment",
        "conversation_comment",
        "proposal_merged",
        "proposal_closed",
        "proposal_reopened",
        "head_pushed",
    )
    assert typing.get_type_hints(ReviewRecord)["reviewer_kind"] == ReviewerKind
    assert typing.get_type_hints(ReviewRecord)["state"] == ReviewState
    assert typing.get_type_hints(ScmFeedbackEvent)["kind"] == FeedbackKind

    with pytest.raises(ValidationError, match="reviewer_kind"):
        _review(reviewer_kind="committee")
    with pytest.raises(ValidationError, match="state"):
        _review(state="merged")
    with pytest.raises(ValidationError, match="kind"):
        _event(kind="release_published")
    with pytest.raises(ValidationError, match="body_digest"):
        _event(body_digest="short")
    with pytest.raises(ValidationError, match="head_sha"):
        _proposal(head_sha="not a sha")
