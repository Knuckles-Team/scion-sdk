"""Delta repository index against the real engine (SCION-SOURCE-INGEST-R004.18)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from live_engine import live_ingest, scoped_client  # noqa: F401
from test_repository_delta_index import _Source

from scion_sdk.repository import DeltaIndexConfig, index_delta

CLOCK = datetime(2026, 11, 26, tzinfo=UTC)
PEAK_BYTES = 67_108_864

FILES = {
    "pkg/a.py": b"def alpha():\n    return 1\n",
    "pkg/b.py": b"from pkg.a import alpha\n\n\ndef beta():\n    return alpha()\n",
    "pkg/c.py": b"def gamma():\n    return 3\n",
}


async def _index(client, source, known, previous, mode):
    return await index_delta(
        client,
        repository="fixture-repo",
        source=source,
        known_digests=known,
        previous=previous,
        config=DeltaIndexConfig(
            batch_files=2, peak_bytes=PEAK_BYTES, semantic_mode=mode
        ),
        layout_revision="v1",
        now=lambda: CLOCK,
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R004.14")
@pytest.mark.spec("SCION-SOURCE-INGEST-R004.18")
async def test_delta_semantics_and_memory(live_ingest) -> None:  # noqa: F811
    client = await scoped_client(["*"])
    try:
        source = _Source(FILES)
        first = await _index(client, source, {}, None, "syntax")
        assert [f.path for f in first.files] == sorted(FILES)
        assert {f.resolution for f in first.files} == {"syntax_only"}
        known = source.digests()
        source.reads.clear()
        source.files["pkg/c.py"] = b"def gamma():\n    return 33\n"
        second = await _index(client, source, known, first.state(), "typed")
        assert source.reads == ["pkg/c.py"]
        assert [f.path for f in second.files] == ["pkg/c.py"]
        assert second.files[0].resolution == "resolved"
        assert second.sequence == 2 and second.semantic_mode == "typed"
        assert second.peak_resident_bytes <= PEAK_BYTES
        assert source.unreleased() == 0
    finally:
        await client.close()
