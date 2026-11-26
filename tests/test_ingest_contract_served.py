"""Ingest runs only under an accepted contract, end to end on the fake engine (R017)."""

from __future__ import annotations

import pytest
from ingest_fakes import SinkBackedEngine

from scion_sdk.ingest import (
    AcceptedContract,
    ChangeSet,
    ContractNotAcceptedError,
    Entity,
    IngestBinding,
    KnowledgeIngest,
    NestedMapping,
)

DIGEST = "a" * 64


class _Engine(SinkBackedEngine):
    def __init__(self, accepted: AcceptedContract | None) -> None:
        super().__init__()
        self.accepted = accepted

    async def accepted_contract(self, connector: str) -> AcceptedContract | None:
        return self.accepted

    async def store_blob(self, data: bytes) -> str:
        return "0" * 64


def _binding(digest: str) -> IngestBinding:
    return IngestBinding(
        connector="demo-mcp",
        stream="demo",
        contract_iri="contract:demo-mcp",
        contract_revision="3",
        contract_digest=digest,
        cursor_policy_iri="policy:demo-cursor",
        nested={"Item.owner": NestedMapping("Person", "ownedBy")},
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R017")
@pytest.mark.spec("SCION-SOURCE-INGEST-R017.6")
async def test_ingest_requires_accepted_contract() -> None:
    engine = _Engine(AcceptedContract(DIGEST, "3"))
    changes = ChangeSet(
        entities=(Entity("i:1", "Item", {"name": "x", "owner": {"name": "ann"}}),)
    )
    await KnowledgeIngest(engine).submit(_binding(DIGEST), changes)
    (request,) = engine.requests
    assert {r.record_id for r in request.records} == {"i:1", "i:1/owner"}
    assert [r.relation_reference for r in request.relationships] == [
        "connector:demo-mcp#relations/Item/ownedBy"
    ]
    status = await engine.source_status("demo-mcp", "demo")
    held = status.accepted_checkpoint
    # A changed local declaration without certification refuses and holds the checkpoint.
    with pytest.raises(ContractNotAcceptedError, match="contract_not_accepted"):
        await KnowledgeIngest(engine).submit(_binding("b" * 64), changes)
    assert len(engine.requests) == 1
    assert (await engine.source_status("demo-mcp", "demo")).accepted_checkpoint == held
