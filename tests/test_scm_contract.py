"""SCION-SCM-R003.1/R003.2: the frozen records and the verified webhook
normalization of the SCM feedback surface."""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import json
import typing
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from scion_sdk.mcp.method_tools import register_method_tools
from scion_sdk.mcp.tool_surface import registered_tools
from scion_sdk.scm import (
    ChangeProposalRef,
    CheckRunRecord,
    ReviewRecord,
    ReviewThreadRecord,
    ScmFeedbackEvent,
)
from scion_sdk.scm.events import (
    SCM_SCHEMES,
    ScmEventNormalizer,
    ScmWebhookRefused,
    verify_webhook,
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


class _StubNormalizer:
    """A connector-side normalizer over a two-action provider payload."""

    provider = "github"

    def normalize(self, headers: Mapping[str, str], body: bytes) -> ScmFeedbackEvent:
        action = json.loads(body)["action"]
        kinds: dict[str, str] = {"pull_request": "proposal_merged"}
        kind = kinds.get(action)
        if kind is None:
            raise ScmWebhookRefused(
                "SCM_EVENT_UNSUPPORTED", "webhook event type is unsupported"
            )
        return ScmFeedbackEvent(
            event_id="evt-2",
            provider="github",
            kind=kind,
            proposal=_proposal(),
            actor="octo",
            actor_kind="person",
            at=NOW,
            body_digest=DIGEST,
        )


@pytest.mark.spec("SCION-SCM-R003.2")
def test_verifier_refuses_bad_signatures_in_constant_time_and_unknown_events(
    monkeypatch,
) -> None:
    """A valid signature or token passes; every other delivery is refused
    with the typed code, in constant time, without the secret in the
    message; the normalizer refuses unsupported event types without
    emitting an event."""
    monkeypatch.setenv("SCM_WEBHOOK_SECRET", "s3cret")
    secret_ref = "env://SCM_WEBHOOK_SECRET"
    body = b'{"action": "pull_request"}'
    signature = "sha256=" + hmac_module.new(b"s3cret", body, hashlib.sha256).hexdigest()
    headers = {"x-hub-signature-256": signature}

    compare_calls: list[tuple[str, str]] = []
    real = hmac_module.compare_digest

    def spy(a: str, b: str) -> bool:
        compare_calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac_module, "compare_digest", spy)

    # The valid hmac_sha256 signature and the valid shared token pass.
    verify_webhook("hmac_sha256", secret_ref, headers, body)
    verify_webhook("shared_token", secret_ref, {"X-Gitlab-Token": "s3cret"}, body)
    assert len(compare_calls) == 2  # both comparisons ran compare_digest

    # Missing or wrong signature, token, or an unknown scheme refuses.
    bad = (
        ("hmac_sha256", {}, body),
        ("hmac_sha256", {"X-Hub-Signature-256": "sha256=" + "0" * 64}, body),
        ("shared_token", {"X-Gitlab-Token": "wrong"}, body),
        ("shared_token", {}, body),
        ("mtls", headers, body),
    )
    for scheme, hdrs, b in bad:
        with pytest.raises(ScmWebhookRefused) as failure:
            verify_webhook(scheme, secret_ref, hdrs, b)
        assert failure.value.code == "SCM_SIGNATURE_INVALID"
        assert "s3cret" not in str(failure.value)

    # The stub normalizer implements the protocol; unknown types refuse.
    stub = _StubNormalizer()
    assert isinstance(stub, ScmEventNormalizer)
    assert stub.normalize(headers, body).kind == "proposal_merged"
    with pytest.raises(ScmWebhookRefused) as failure:
        stub.normalize(headers, b'{"action": "release_published"}')
    assert failure.value.code == "SCM_EVENT_UNSUPPORTED"

    assert SCM_SCHEMES == ("hmac_sha256", "shared_token")


@pytest.mark.spec("SCION-SCM-R003.3")
async def test_merge_tools_need_the_flag_and_refuse_without_approval_ref() -> None:
    """Registration refuses a merge method without the flag; a flagged tool
    carries the flag in its metadata and refuses a call without
    approval_ref before it calls the client."""
    calls: list[tuple[str, int]] = []

    class MergeApi:
        def merge_pull_request(self, number: int = 1) -> str:
            """Merge the pull request."""
            calls.append(("merge_pull_request", number))
            return f"merged {number}"

    # A merge method without the flag refuses registration.
    mcp = FastMCP("demo")
    with pytest.raises(ValueError, match="TOOL_APPROVAL_FLAG_MISSING"):
        register_method_tools(mcp, MergeApi, lambda: MergeApi(), prefix="demo")

    # Reads and cancel_/disable_ names are exempt from the rule.
    class ExemptApi:
        def list_merged_pull_requests(self) -> list[str]:
            """List the merged pull requests."""
            return []

        def cancel_merge_queue(self) -> str:
            """Cancel the merge queue."""
            return "cancelled"

        def disable_merge_bot(self) -> str:
            """Disable the merge bot."""
            return "disabled"

    mcp_exempt = FastMCP("exempt")
    names = register_method_tools(
        mcp_exempt, ExemptApi, lambda: ExemptApi(), prefix="demo"
    )
    assert set(names) == {
        "demo_list_merged_pull_requests",
        "demo_cancel_merge_queue",
        "demo_disable_merge_bot",
    }

    # Flagged, the merge tool carries the flag in its metadata.
    manifest = [
        {
            "method": "merge_pull_request",
            "http": "POST /merge",
            "owner_approval_required": True,
        }
    ]
    mcp_flagged = FastMCP("flagged")
    register_method_tools(
        mcp_flagged, MergeApi, lambda: MergeApi(), prefix="demo", manifest=manifest
    )
    tool = registered_tools(mcp_flagged)["demo_merge_pull_request"]
    assert tool.meta == {"owner_approval_required": True}

    # Without approval_ref the call is refused; the client is never called.
    refused = await tool.fn(number=7, ctx=None)
    assert refused["cancelled"] is True
    assert refused["operation"] == "merge_pull_request"
    assert refused["code"] == "approval_required"
    assert calls == []

    # With approval_ref the client is called.
    assert await tool.fn(number=7, approval_ref="approval-42", ctx=None) == "merged 7"
    assert calls == [("merge_pull_request", 7)]
