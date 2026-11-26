"""The reconciliation client against a loopback service (R054)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse
from scion_sdk.transports import ReconciliationClient, ReconciliationError

ENDPOINT = "http://127.0.0.1:9/reconcile"
SPACE = "http://example.test/entity/"


def _client(handler: httpx.MockTransport) -> ReconciliationClient:
    options = HttpClientOptions(
        base_url="http://127.0.0.1:9", retry=RetryPolicy(max_attempts=1)
    )
    return ReconciliationClient(options, transport=handler)


def _service(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json={"identifierSpace": SPACE})
    form = parse_qs(request.content.decode())
    query = json.loads(form["queries"][0])["q0"]
    if query["query"] == "nobody":
        return httpx.Response(200, json={"q0": {"result": []}})
    if query["query"] == "html":
        return httpx.Response(200, content="<html>")
    if query["query"] == "denied":
        return httpx.Response(403, content="planted")
    results = [
        {"id": "Q2", "name": "Second", "score": 41.5, "match": False},
        {"id": "Q1", "name": "First", "description": "d", "score": 99, "match": True},
    ]
    return httpx.Response(200, json={"q0": {"result": results}})


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R054.1")
def test_candidates_resolve_ids_and_each_failure_category() -> None:
    client = _client(httpx.MockTransport(_service))
    found = client.reconcile(ENDPOINT, "someone", type="Person", limit=5)
    assert [c.entity for c in found] == [SPACE + "Q2", SPACE + "Q1"]
    assert found[1].score == Decimal("99") and found[1].match is True
    assert client.reconcile(ENDPOINT, "nobody") == []
    for name, category in (("html", "NON_JSON_RESPONSE"), ("denied", "READ_REJECTED")):
        with pytest.raises(ReconciliationError) as caught:
            client.reconcile(ENDPOINT, name)
        assert caught.value.category.value == category
        assert "planted" not in str(caught.value)
    bare = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    with pytest.raises(ReconciliationError) as caught:
        bare.reconcile(ENDPOINT, "x")
    assert caught.value.category.value == "MANIFEST_INVALID"
    with pytest.raises(ValueError, match="limit"):
        client.reconcile(ENDPOINT, "x", limit=0)


@dataclass(frozen=True)
class EntityLinkProposal:
    """What the recording engine adapter receives: a proposal, never an accepted link."""

    entity: str
    score: Decimal
    accepted: bool = False


class RecordingAdapter:
    def __init__(self) -> None:
        self.proposals: list[EntityLinkProposal] = []
        self.accepted_links: list[str] = []

    def propose(self, proposal: EntityLinkProposal) -> None:
        self.proposals.append(proposal)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R054")
@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R054.2")
def test_loopback_service_returns_ranked_candidates() -> None:
    manifest = ScriptedResponse(
        body=json.dumps({"identifierSpace": SPACE}).encode(),
        headers={"content-type": "application/json"},
    )
    answer = ScriptedResponse(
        body=json.dumps(
            {
                "q0": {
                    "result": [
                        {"id": "Q1", "name": "First", "score": 99, "match": True},
                        {"id": "Q2", "name": "Second", "score": 41.5, "match": False},
                    ]
                }
            }
        ).encode(),
        headers={"content-type": "application/json"},
    )
    adapter = RecordingAdapter()
    with ScriptedHttpServer(manifest, answer) as service:
        options = HttpClientOptions(
            base_url=service.base_url, retry=RetryPolicy(max_attempts=1)
        )
        candidates = ReconciliationClient(options).reconcile(
            f"{service.base_url}/reconcile", "someone"
        )
        assert [r.method for r in service.requests] == ["GET", "POST"]
    assert [c.entity for c in candidates] == [SPACE + "Q1", SPACE + "Q2"]
    assert candidates[0].score > candidates[1].score and candidates[0].match
    best = candidates[0]
    adapter.propose(EntityLinkProposal(best.entity, best.score))
    assert adapter.proposals == [EntityLinkProposal(SPACE + "Q1", Decimal("99"))]
    assert adapter.proposals[0].accepted is False and adapter.accepted_links == []
