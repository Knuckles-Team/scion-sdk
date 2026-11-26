"""Instance profile of a SPARQL endpoint against a loopback endpoint (R053)."""

from __future__ import annotations

import importlib
import json
from typing import Any

import httpx
import pytest

from scion_sdk.engine_capability import (
    EngineCapabilityUnavailable,
    engine_capability_present,
)
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.ontology.instance_profile import (
    InstanceProfileError,
    build_instance_profile,
    submit_instance_profile,
)
from scion_sdk.transports.sparql import SparqlClient

EX = "http://example.test/"


def _cell(value: str, kind: str = "uri") -> dict[str, str]:
    return {"type": kind, "value": value}


def _endpoint(request: httpx.Request) -> httpx.Response:
    query = request.content.decode()
    if "GROUP BY ?class ORDER" in query and "?instances" in query:
        rows = [{"class": _cell(EX + "Person"), "instances": _cell("4", "literal")}]
        head = ["class", "instances"]
    elif "FILTER(isLiteral" in query:
        rows = [
            {
                "class": _cell(EX + "Person"),
                "property": _cell(EX + "name"),
                "datatype": _cell("http://www.w3.org/2001/XMLSchema#string"),
                "carriers": _cell("3", "literal"),
            }
        ]
        head = ["class", "property", "datatype", "carriers"]
    else:
        rows = [
            {
                "domain": _cell(EX + "Person"),
                "property": _cell(EX + "knows"),
                "range": _cell(EX + "Person"),
                "usage": _cell("7", "literal"),
            }
        ]
        head = ["domain", "property", "range", "usage"]
    body = {"head": {"vars": head}, "results": {"bindings": rows}}
    return httpx.Response(
        200, content=json.dumps(body), headers={"content-type": "application/json"}
    )


def _client(handler: Any = _endpoint) -> SparqlClient:
    options = HttpClientOptions(
        base_url="http://127.0.0.1:9", retry=RetryPolicy(max_attempts=1)
    )
    return SparqlClient(
        "http://127.0.0.1:9/sparql", options, transport=httpx.MockTransport(handler)
    )


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R053.1")
def test_three_queries_bounds_and_truncation_refusal() -> None:
    queries: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        queries.append(request.content.decode())
        return _endpoint(request)

    profile = build_instance_profile(_client(recording), max_entries=5)
    assert len(queries) == 3 and all("LIMIT 5" in q for q in queries)
    assert profile.classes[0].instances == 4
    assert profile.datatype_properties[0].presence == 0.75
    assert profile.object_properties[0].usage == 7
    with pytest.raises(InstanceProfileError) as caught:
        build_instance_profile(_client(), max_entries=1)
    assert caught.value.code == "PROFILE_TRUNCATED"


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R053")
@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R053.2")
async def test_submission_is_refused_while_the_pinned_engine_lacks_the_method() -> None:
    sent: list[dict[str, Any]] = []

    async def send(batch: dict[str, Any]) -> object:
        sent.append(batch)
        return None

    profile = build_instance_profile(_client())
    if engine_capability_present("ProfileSubmission"):
        pytest.skip("the pin serves ProfileSubmission")
    with pytest.raises(EngineCapabilityUnavailable) as caught:
        await submit_instance_profile(profile, proposal_id="p1", send=send)
    assert caught.value.method == "ProfileSubmission"
    assert sent == []


@pytest.mark.skipif(
    not engine_capability_present("ProfileSubmission"),
    reason="runs once the pinned engine client serves ProfileSubmission",
)
async def test_profile_from_loopback_endpoint_matches_the_wire_model() -> None:
    ProfileSubmission = importlib.import_module(
        "medulla.generated.source_ingestion"
    ).ProfileSubmission

    sent: list[dict[str, Any]] = []

    async def send(batch: dict[str, Any]) -> object:
        sent.append(batch)
        return None

    profile = build_instance_profile(_client())
    await submit_instance_profile(profile, proposal_id="p1", send=send)
    assert len(sent) == 1
    (element,) = sent[0]["proposals"]
    assert ProfileSubmission.model_validate(element).profile.classes[0].instances == 4
