"""Frozen change-proposal records of the SCM feedback surface.

SCION-SCM-R003.1: the typed records of a change proposal and its review,
thread and check feedback. Every record is frozen; the closed sets
(reviewer kind, review state, feedback kind) refuse unknown values with
the field name in the error. A feedback event travels with the
SHA-256 digest of its raw body, never the body itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_SHA = r"^[0-9a-f]{40,64}$"
_DIGEST = r"^[0-9a-f]{64}$"

ReviewerKind = Literal["person", "automated"]
SCM_REVIEWER_KINDS: tuple[str, ...] = ("person", "automated")

ReviewState = Literal["approved", "changes_requested", "commented", "dismissed"]
SCM_REVIEW_STATES: tuple[str, ...] = (
    "approved",
    "changes_requested",
    "commented",
    "dismissed",
)

FeedbackKind = Literal[
    "check_changed",
    "review_submitted",
    "review_comment",
    "conversation_comment",
    "proposal_merged",
    "proposal_closed",
    "proposal_reopened",
    "head_pushed",
]
SCM_FEEDBACK_KINDS: tuple[FeedbackKind, ...] = (
    "check_changed",
    "review_submitted",
    "review_comment",
    "conversation_comment",
    "proposal_merged",
    "proposal_closed",
    "proposal_reopened",
    "head_pushed",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ChangeProposalRef(_Model):
    """A provider change proposal (pull or merge request) at one head."""

    provider: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    number: int = Field(ge=1)
    head_sha: str = Field(pattern=_SHA)
    base_ref: str = Field(min_length=1)
    state: str = Field(min_length=1)
    author: str = Field(min_length=1)
    changed_lines: int = Field(ge=0)


class ReviewRecord(_Model):
    """One review decision on a proposal."""

    id: str = Field(min_length=1)
    proposal: ChangeProposalRef
    reviewer: str = Field(min_length=1)
    reviewer_kind: ReviewerKind
    state: ReviewState
    commit_sha: str = Field(pattern=_SHA)
    submitted_at: datetime
    run_key: str = Field(min_length=1)


class ReviewThreadRecord(_Model):
    """One review thread on a file path."""

    id: str = Field(min_length=1)
    proposal: ChangeProposalRef
    path: str = Field(min_length=1)
    line: int = Field(ge=1)
    resolved: bool
    comments: tuple[str, ...]


class CheckRunRecord(_Model):
    """One check run on a proposal head."""

    id: str = Field(min_length=1)
    proposal: ChangeProposalRef
    head_sha: str = Field(pattern=_SHA)
    name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ScmFeedbackEvent(_Model):
    """One normalized webhook feedback event; ``event_id`` is the
    engine's idempotency key and ``body_digest`` digests the raw body."""

    event_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    kind: FeedbackKind
    proposal: ChangeProposalRef
    actor: str = Field(min_length=1)
    actor_kind: str = Field(min_length=1)
    at: datetime
    body_digest: str = Field(pattern=_DIGEST)
