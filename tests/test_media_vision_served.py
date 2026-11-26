"""Media tags from a producer tool served over real MCP reach the real engine (R016.6)."""

import asyncio

import pytest
from fastmcp import FastMCP
from live_engine import live_ingest, scoped_client  # noqa: F401
from source_graph_helpers import import_source_mapping
from test_contracts_and_sink import _catalog

from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
from scion_sdk.adapters.media_tags import (
    MediaInput,
    MediaTagProducer,
    MediaTagRefusal,
    MediaTagRefusalCode,
    media_tag_records,
    provenance_digest,
    tag_media,
)
from scion_sdk.contracts import ExternalAccess
from scion_sdk.manifest.presets import ToolPreset
from scion_sdk.sinks.medulla import MedullaSink

CONNECTOR = "media-vision-demo"
PRODUCER = MediaTagProducer(
    producer_id="vision",
    producer_revision="r1",
    input_modalities=("image", "thumbnail"),
    taxonomy_id="tax",
    taxonomy_version="1",
    max_tags_per_item=4,
)
ACCESS = ExternalAccess(user_emails=["owner@example.test"])


def _media(item, modality="image", digest="a"):
    return MediaInput(
        media_item=item,
        media_revision="v1",
        modality=modality,
        size_bytes=10,
        width_px=64,
        height_px=64,
        input_digest=digest * 64,
    )


def _raw(media, label, **over):
    raw = {
        "media_item": media.media_item,
        "media_revision": media.media_revision,
        "taxonomy_id": "tax",
        "taxonomy_version": "1",
        "object_or_context": "object",
        "label": label,
        "confidence": 0.8,
        "evidence_ref": "region:1",
        "opaque_producer_id": "vision",
        "producer_revision": "r1",
        "provenance_digest": provenance_digest(
            media.input_digest, PRODUCER, "tax", label, 0.8
        ),
        "origin": "inferred",
    }
    raw.update(over)
    return raw


async def _outcomes(inputs, labels):
    """Tag each input once; a refused input yields no record, only its refusal."""
    records, refusals = [], []
    for media in inputs:

        async def call(m, labels=labels):
            if m.media_item == "denied":
                raise PermissionError("not authorized")
            return [_raw(m, label) for label in labels] + (
                [_raw(m, "fake", provenance_digest="0" * 64)]
                if m.media_item == "fabricated"
                else []
            )

        try:
            tags = await tag_media(
                PRODUCER, media, call, registered=frozenset({"vision"})
            )
        except MediaTagRefusal as refusal:
            refusals.append((media.media_item, refusal.code))
        else:
            records.extend(media_tag_records(tags, ACCESS))
    return records, refusals


async def _exercise():
    inputs = [
        _media("img-1"),
        _media("thumb-1", "thumbnail", "b"),
        _media("clip-1", "video", "c"),
        _media("denied", digest="d"),
        _media("fabricated", digest="e"),
    ]
    records, refusals = await _outcomes(inputs, ["cat", "dog"])
    again, _ = await _outcomes(inputs, ["dog", "cat"])
    assert records == again  # identical media reingests as an identical tag set
    server = FastMCP("media")

    @server.tool()
    def media_tags() -> dict[str, object]:
        return {"records": records, "next": None}

    engine = await scoped_client(["*"])
    try:
        context = await import_source_mapping(
            engine,
            CONNECTOR,
            {
                "MediaTag": {
                    "fields": {"label": "xsd:string"},
                    "ontology_class": "Document",
                }
            },
        )

        async def authority(_connector):
            return _catalog(), context

        preset = ToolPreset(
            name="media_tags",
            server=CONNECTOR,
            tool="media_tags",
            id_field="id",
            records_path="records",
            params_style="args",
            params={},
            pagination="none",
        )

        def adapter():
            return McpToolSourceAdapter(
                preset,
                connector=CONNECTOR,
                mapping_reference=f"connector:{CONNECTOR}#mappings/MediaTag",
            )

        sink = MedullaSink(engine, authority)
        from scion_sdk.ports.session import TransportEndpoint
        from scion_sdk.runner.syncing import SyncTarget, sync_stream
        from scion_sdk.transports.mcp import McpTransport

        target = SyncTarget(CONNECTOR, sink, max_pages=1)
        async with McpTransport().session(TransportEndpoint(in_process=server)) as s:
            first = await sync_stream(s, adapter(), target)
            second = await sync_stream(s, adapter(), target)
        rows = await engine.query.cypher_read("MATCH (n) RETURN n LIMIT 100")
        return records, refusals, first, second, rows
    finally:
        await engine.close()


@pytest.mark.spec("SCION-SOURCE-INGEST-R016")
@pytest.mark.spec("SCION-SOURCE-INGEST-R016.6")
def test_image_inference_and_attachment(live_ingest):  # noqa: F811
    records, refusals, first, second, rows = asyncio.run_coroutine_threadsafe(
        _exercise(), live_ingest._loop
    ).result(60)
    assert dict(refusals) == {
        "clip-1": MediaTagRefusalCode.MODALITY_UNSUPPORTED,
        "denied": MediaTagRefusalCode.TAG_UNAUTHORIZED,
        "fabricated": MediaTagRefusalCode.TAG_FABRICATED,
    }
    # image and thumbnail each carry two tags; no record exists for a refused input
    assert len(records) == 4
    assert {r["media_item"] for r in records} == {"img-1", "thumb-1"}
    assert first.accepted == 4 and second.accepted == 4
    nodes = [n["n"] for n in rows if n["n"].get("source_stream") == "media_tags"]
    assert len(nodes) == 4, nodes  # identical reingest is stable, no duplicates
    assert len({r["id"] for r in records}) == 4
