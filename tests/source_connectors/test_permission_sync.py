"""Access, group, enumeration and container batches (SCION-SOURCE-INGEST-R805.1 to R805.3)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from scion_sdk.contracts import ExternalAccess
from scion_sdk.runner.governance import (
    ACCESS_MARKINGS_UNSUPPORTED,
    AccessSnapshot,
    ConnectorGovernance,
    GovernanceSchedule,
    map_access,
    run_enumeration,
    sync_governance,
)
from scion_sdk.source_connectors import (
    HierarchyConnector,
    PermSyncConnector,
    SlimDocument,
    SourceContainer,
    SourceGroup,
)
from scion_sdk.testing.sinks import InMemorySink

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class _Perms(PermSyncConnector):
    source_type = "demo"

    def __init__(self, fail_after: int | None = None) -> None:
        super().__init__()
        self.fail_after = fail_after

    def slim(self) -> Iterator[SlimDocument]:
        for index, doc_id in enumerate(("d3", "d1", "d2")):
            if self.fail_after is not None and index >= self.fail_after:
                raise RuntimeError("source broke")
            yield SlimDocument(id=doc_id)

    def fetch_access(self) -> Iterator[tuple[str, ExternalAccess]]:
        yield (
            "d1",
            ExternalAccess(
                user_emails=["a@x.test"], group_ids=["g1"], read_roles=["admin"]
            ),
        )
        yield "d2", ExternalAccess(is_public=True)
        yield "d3", ExternalAccess(markings=["secret"])


class _Tree(HierarchyConnector):
    source_type = "demo-tree"

    def containers(self) -> Iterator[SourceContainer]:
        yield SourceContainer(source="demo", container_id="root", title="Root")
        yield SourceContainer(source="demo", container_id="mid", parent_id="root")
        yield SourceContainer(source="demo", container_id="leaf", parent_id="mid")


def _extensions(
    sink: InMemorySink, kind: str
) -> list[dict[str, list[dict[str, object]]]]:
    return [
        b.extension
        for b in sink.batches.values()
        if b.kind.value == kind and b.extension
    ]


@pytest.mark.spec("SCION-SOURCE-INGEST-R805.1")
async def test_access_batch_carries_no_content() -> None:
    snapshots, unsupported = map_access("demo", _Perms().fetch_access(), NOW)
    assert unsupported == ["d3"] and len(snapshots) == 2
    first = snapshots[0]
    assert isinstance(first, AccessSnapshot)
    assert first.read_principals == ("a@x.test",)
    assert first.read_groups == ("g1", "role:admin")
    assert snapshots[1].access_public is True
    assert not hasattr(first, "text") and "body" not in first.model_dump()
    assert list(_Perms().fetch_groups()) == []
    sink = InMemorySink()
    source = ConnectorGovernance("demo", _Perms(), _Tree())
    report = await sync_governance(
        source, sink, NOW, GovernanceSchedule(), connector="demo-mcp"
    )
    assert report.codes == [ACCESS_MARKINGS_UNSUPPORTED]
    (access,) = _extensions(sink, "access")
    assert {row["record_ref"] for row in access["access"]} == {"d1", "d2"}
    assert SourceGroup(group_id="g").members == []


@pytest.mark.spec("SCION-SOURCE-INGEST-R805.2")
async def test_interrupted_enumeration_is_incomplete() -> None:
    sink = InMemorySink()
    perms = _Perms(fail_after=2)
    ids = (d.id for d in perms.slim())
    outcome = await run_enumeration(sink, "demo-mcp", ids, run_id="r1")
    assert outcome.complete is False and outcome.count == 2
    (closing,) = _extensions(sink, "enumeration")
    assert closing["enumeration"][0]["complete"] is False
    sink2 = InMemorySink()
    done = await run_enumeration(
        sink2, "demo-mcp", (d.id for d in _Perms().slim()), run_id="r2"
    )
    expected = hashlib.sha256(b"d1\nd2\nd3").hexdigest()
    assert done.complete is True and done.ids_digest == expected and done.count == 3
    (closing2,) = _extensions(sink2, "enumeration")
    assert closing2["enumeration"][0] == {"complete": True, "ids_digest": expected}
    modes = {b.mode.value for b in sink2.batches.values() if b.kind.value == "records"}
    assert modes == {"reconcile"}


@pytest.mark.spec("SCION-SOURCE-INGEST-R805.3")
async def test_container_batch_preserves_parents() -> None:
    sink = InMemorySink()
    source = ConnectorGovernance("demo", _Perms(), _Tree())
    await sync_governance(source, sink, NOW, GovernanceSchedule(), connector="demo-mcp")
    (containers,) = _extensions(sink, "containers")
    rows = containers["containers"]
    assert [r["container_id"] for r in rows] == ["root", "mid", "leaf"]
    assert [r["parent_id"] for r in rows] == [None, "root", "mid"]
