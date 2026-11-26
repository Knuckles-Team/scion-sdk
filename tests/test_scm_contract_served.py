"""The SCM change-proposal contract served through two fixture
connectors (SCION-SCM-R003.5).

One fixture connector proves its webhooks with ``hmac_sha256``, the other
with ``shared_token``. Each declares a flagged merge tool and registers
its normalizer; through an MCP client one signed event per kind, one bad
signature and one unknown event are posted, and each merge tool is
called with and without ``approval_ref``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac as hmac_module
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

import pytest
from fastmcp import Client, FastMCP
from mcp.types import TextContent

from scion_sdk.connector_hooks import RegistrationContext
from scion_sdk.mcp.tool_surface import register_tool_surface
from scion_sdk.scm import (
    ChangeProposalRef,
    ScmFeedbackEvent,
    ScmWebhookRefused,
    register_scm_normalizer,
)
from scion_sdk.scm.records import SCM_FEEDBACK_KINDS

DIGEST = "b" * 64
NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def _proposal(provider: str) -> ChangeProposalRef:
    return ChangeProposalRef(
        provider=provider,
        repository="acme/widgets",
        number=42,
        head_sha="a" * 40,
        base_ref="main",
        state="open",
        author="octo",
        changed_lines=120,
    )


class _FixtureNormalizer:
    """Maps each of the eight feedback kinds onto itself; refuses the rest."""

    def __init__(self, provider: str, deliveries: list[bytes]) -> None:
        self.provider = provider
        self._deliveries = deliveries

    def normalize(self, headers: Mapping[str, str], body: bytes) -> ScmFeedbackEvent:
        self._deliveries.append(body)
        action = json.loads(body)["action"]
        if action not in SCM_FEEDBACK_KINDS:
            raise ScmWebhookRefused(
                "SCM_EVENT_UNSUPPORTED", "webhook event type is unsupported"
            )
        return ScmFeedbackEvent(
            event_id=f"evt-{action}",
            provider=self.provider,
            kind=action,
            proposal=_proposal(self.provider),
            actor="octo",
            actor_kind="person",
            at=NOW,
            body_digest=DIGEST,
        )


class _MergeClient:
    """The fixture connector's client; records every merge call."""

    def __init__(self, calls: list[tuple[int, str]]) -> None:
        self._calls = calls

    def merge_pull_request(self, number: int = 1) -> str:
        """Merge the pull request.

        POST /merge
        """
        self._calls.append((number, "merge"))
        return f"merged {number}"


def _signed_headers(
    scheme: str, secret: str, body: bytes
) -> tuple[dict[str, str], str]:
    """The delivery headers and base64 body for one signed post."""
    if scheme == "hmac_sha256":
        signature = (
            "sha256="
            + hmac_module.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        return {"X-Hub-Signature-256": signature}, base64.b64encode(body).decode()
    return {"X-Gitlab-Token": secret}, base64.b64encode(body).decode()


def _serve_fixture(
    service: str,
    scheme: Literal["hmac_sha256", "shared_token"],
    client: _MergeClient,
    deliveries: list[bytes],
) -> FastMCP[Any]:
    """One fixture connector: a flagged merge tool and a normalizer."""
    connector: FastMCP[Any] = FastMCP(service)
    register_tool_surface(
        connector,
        service=service,
        client_cls=_MergeClient,
        get_client=lambda: client,
        manifest=[
            {
                "method": "merge_pull_request",
                "http": "POST /merge",
                "owner_approval_required": True,
            }
        ],
    )
    register_scm_normalizer(_FixtureNormalizer(service, deliveries), scheme=scheme)(
        RegistrationContext(
            server=connector,
            name=service,
            version="0.0.0",
            settings=argparse.Namespace(),
            client_factory=None,
            auth=None,
        )
    )
    return connector


async def _exercise(
    caller: Client[Any],
    prefix: str,
    scheme: Literal["hmac_sha256", "shared_token"],
    secret: str,
    deliveries: list[bytes],
    merge_calls: list[tuple[int, str]],
) -> None:
    """One signed event per kind, one bad signature, one unknown event,
    then the merge tool with and without approval_ref."""
    normalize = f"{prefix}_normalize_scm_event"

    for kind in SCM_FEEDBACK_KINDS:
        headers, body = _signed_headers(
            scheme, secret, json.dumps({"action": kind}).encode()
        )
        event = await caller.call_tool(normalize, {"headers": headers, "body": body})
        provider = prefix.replace("_", "-")
        expected = ScmFeedbackEvent(
            event_id=f"evt-{kind}",
            provider=provider,
            kind=kind,
            proposal=_proposal(provider),
            actor="octo",
            actor_kind="person",
            at=NOW,
            body_digest=DIGEST,
        ).model_dump(mode="json")
        assert event.structured_content == expected

    # A bad signature refuses with SCM_SIGNATURE_INVALID; no event results.
    bad_body = json.dumps({"action": "head_pushed"}).encode()
    if scheme == "hmac_sha256":
        bad_headers = {"X-Hub-Signature-256": "sha256=" + "0" * 64}
        bad_reason = "webhook signature is invalid"
    else:
        bad_headers = {"X-Gitlab-Token": "wrong-token"}
        bad_reason = "webhook token is invalid"
    refused = await caller.call_tool(
        normalize, {"headers": bad_headers, "body": base64.b64encode(bad_body).decode()}
    )
    assert refused.structured_content == {
        "refused": True,
        "code": "SCM_SIGNATURE_INVALID",
        "reason": bad_reason,
    }
    assert len(deliveries) == 8  # the verifier refused before the normalizer

    # An unknown event type refuses with SCM_EVENT_UNSUPPORTED.
    headers, body = _signed_headers(
        scheme, secret, json.dumps({"action": "release_published"}).encode()
    )
    refused = await caller.call_tool(normalize, {"headers": headers, "body": body})
    assert refused.structured_content == {
        "refused": True,
        "code": "SCM_EVENT_UNSUPPORTED",
        "reason": "webhook event type is unsupported",
    }

    # The flagged merge tool refuses without approval_ref; zero client calls.
    merge = f"{prefix}_merge_pull_request"
    refused = await caller.call_tool(merge, {"number": 7})
    assert refused.structured_content == {
        "cancelled": True,
        "operation": "merge_pull_request",
        "code": "approval_required",
        "reason": (
            "owner approval required: repeat the call with the approval_ref argument"
        ),
    }
    assert merge_calls == []

    # With approval_ref the merge runs through the client.
    merged = await caller.call_tool(merge, {"number": 7, "approval_ref": "approval-9"})
    merged_first = merged.content[0]
    assert isinstance(merged_first, TextContent)
    assert merged_first.text == "merged 7"
    assert merge_calls == [(7, "merge")]


@pytest.mark.spec("SCION-SCM-R003.5")
async def test_two_fixture_connectors_normalize_and_flag_merge_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both schemes serve one typed event per valid post, the typed
    refusals and zero client calls on refusal."""
    monkeypatch.setenv("SCM_WEBHOOK_SECRET_A", "secret-a")
    monkeypatch.setenv("SCM_WEBHOOK_SECRET_B", "secret-b")
    monkeypatch.setenv("SCM_WEBHOOK_SECRET_REF", "env://SCM_WEBHOOK_SECRET_A")

    deliveries_a: list[bytes] = []
    merge_calls_a: list[tuple[int, str]] = []
    connector_a = _serve_fixture(
        "fixture-a", "hmac_sha256", _MergeClient(merge_calls_a), deliveries_a
    )
    async with Client(connector_a) as caller:
        await _exercise(
            caller, "fixture_a", "hmac_sha256", "secret-a", deliveries_a, merge_calls_a
        )

    monkeypatch.setenv("SCM_WEBHOOK_SECRET_REF", "env://SCM_WEBHOOK_SECRET_B")
    deliveries_b: list[bytes] = []
    merge_calls_b: list[tuple[int, str]] = []
    connector_b = _serve_fixture(
        "fixture-b", "shared_token", _MergeClient(merge_calls_b), deliveries_b
    )
    async with Client(connector_b) as caller:
        await _exercise(
            caller, "fixture_b", "shared_token", "secret-b", deliveries_b, merge_calls_b
        )
