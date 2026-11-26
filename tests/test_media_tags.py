"""Media tags: bounded, validated, stable and never silent (SC-R016)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from scion_sdk.adapters.media_tags import (
    CONTRACT_REVISION,
    MEDIA_TAG_MAX_PER_ITEM,
    MediaInput,
    MediaTag,
    MediaTagFailure,
    MediaTagProducer,
    MediaTagRefusal,
    MediaTagRefusalCode,
    provenance_digest,
    tag_media,
    tag_record_id,
)

PRODUCER = MediaTagProducer(
    producer_id="vision",
    producer_revision="r1",
    input_modalities=("image", "thumbnail"),
    taxonomy_id="tax",
    taxonomy_version="1",
    max_tags_per_item=2,
)
MEDIA = MediaInput(
    media_item="m1",
    media_revision="v1",
    modality="image",
    size_bytes=10,
    width_px=100,
    height_px=100,
    input_digest="a" * 64,
)


def _raw(label, confidence=0.5, **over):
    raw = {
        "media_item": "m1",
        "media_revision": "v1",
        "taxonomy_id": "tax",
        "taxonomy_version": "1",
        "object_or_context": "object",
        "label": label,
        "confidence": confidence,
        "evidence_ref": "region:1",
        "opaque_producer_id": "vision",
        "producer_revision": "r1",
        "provenance_digest": provenance_digest(
            "a" * 64, PRODUCER, "tax", label, confidence
        ),
        "origin": "inferred",
    }
    raw.update(over)
    return raw


def _tag(raws, media=MEDIA, producer=PRODUCER, registered=frozenset({"vision"})):
    async def call(_media):
        if isinstance(raws, Exception):
            raise raws
        return raws

    return asyncio.run(tag_media(producer, media, call, registered=registered))


def _refusal(**kwargs):
    with pytest.raises(MediaTagRefusal) as raised:
        _tag(**kwargs)
    return raised.value


@pytest.mark.spec("SCION-SOURCE-INGEST-R016.1")
def test_models_reject_unknown_modality_missing_evidence_and_bad_confidence():
    with pytest.raises(ValidationError):
        MediaTagProducer(
            producer_id="p",
            producer_revision="r",
            input_modalities=("video",),
            taxonomy_id="t",
            taxonomy_version="1",
        )
    with pytest.raises(ValidationError):
        MediaTagProducer(
            producer_id="p",
            producer_revision="r",
            input_modalities=("image",),
            taxonomy_id="t",
            taxonomy_version="1",
            max_bytes=52_428_801,
        )
    with pytest.raises(ValidationError):
        MediaTag.model_validate(_raw("cat", evidence_ref=""))
    with pytest.raises(ValidationError):
        MediaTag.model_validate(_raw("cat", confidence=1.5))
    assert PRODUCER.contract_revision == CONTRACT_REVISION
    assert MEDIA_TAG_MAX_PER_ITEM == 64


@pytest.mark.spec("SCION-SOURCE-INGEST-R016.2")
def test_tags_are_sorted_bounded_deduplicated_and_stable():
    raws = [_raw("dog", 0.9), _raw("cat", 0.8), _raw("cat", 0.8), _raw("sky", 0.1)]
    first = _tag(raws)
    assert [t.label for t in first] == ["cat", "dog"]  # sky dropped by the bound
    again = _tag(list(reversed(raws)))
    assert first == again
    assert [tag_record_id(t) for t in first] == [tag_record_id(t) for t in again]
    assert len({tag_record_id(t) for t in first}) == 2
    assert _tag([_raw("low", 0.0)])[0].confidence == 0.0  # no confidence threshold


@pytest.mark.spec("SCION-SOURCE-INGEST-R016.4")
@pytest.mark.parametrize(
    ("code", "kwargs"),
    [
        (MediaTagRefusalCode.PRODUCER_UNKNOWN, {"raws": [], "registered": frozenset()}),
        (
            MediaTagRefusalCode.MODALITY_UNSUPPORTED,
            {"raws": [], "media": MEDIA.model_copy(update={"modality": "video"})},
        ),
        (
            MediaTagRefusalCode.MEDIA_TOO_LARGE,
            {"raws": [], "media": MEDIA.model_copy(update={"size_bytes": 10**9})},
        ),
        (
            MediaTagRefusalCode.DIMENSIONS_EXCEEDED,
            {"raws": [], "media": MEDIA.model_copy(update={"width_px": 99999})},
        ),
        (MediaTagRefusalCode.TAG_UNAUTHORIZED, {"raws": PermissionError("no")}),
        (MediaTagRefusalCode.TAG_FABRICATED, {"raws": [_raw("cat", evidence_ref="")]}),
        (
            MediaTagRefusalCode.TAG_FABRICATED,
            {"raws": [_raw("cat", opaque_producer_id="other")]},
        ),
        (
            MediaTagRefusalCode.CONTRACT_MISMATCH,
            {"raws": [_raw("cat", taxonomy_version="9")]},
        ),
        (
            MediaTagRefusalCode.CONTRACT_MISMATCH,
            {
                "raws": [],
                "producer": PRODUCER.model_copy(update={"contract_revision": "old"}),
            },
        ),
        (
            MediaTagRefusalCode.PRODUCER_CAPABILITY_INSUFFICIENT,
            {"raws": RuntimeError("model missing")},
        ),
    ],
)
def test_each_refusal_is_closed_and_leaves_a_failure_record(code, kwargs):
    refusal = _refusal(**kwargs)
    assert refusal.code is code
    assert refusal.failure == MediaTagFailure(code=code, input_digest="a" * 64)


@pytest.mark.spec("SCION-SOURCE-INGEST-R016.5")
def test_tags_bind_provenance_and_failures_carry_no_content():
    (tag,) = _tag([_raw("cat")])
    assert tag.provenance_digest == provenance_digest(
        "a" * 64, PRODUCER, "tax", "cat", 0.5
    )
    assert (tag.opaque_producer_id, tag.producer_revision) == ("vision", "r1")
    failure = _refusal(raws=RuntimeError("secret-token")).failure
    assert set(failure.model_dump()) == {"code", "input_digest"}
    with pytest.raises(ValidationError):
        MediaTagFailure(code="X", input_digest="a" * 64, detail="raw")  # type: ignore[call-arg]


@pytest.mark.spec("SCION-SOURCE-INGEST-R016.3")
def test_sync_maps_tags_with_media_access():
    from fastmcp import FastMCP

    from scion_sdk.adapters.mcp_tool import McpToolSourceAdapter
    from scion_sdk.adapters.media_tags import media_tag_records
    from scion_sdk.contracts import ExternalAccess
    from scion_sdk.manifest.presets import ToolPreset
    from scion_sdk.ports.session import TransportEndpoint
    from scion_sdk.transports.mcp import McpTransport

    access = ExternalAccess(user_emails=["a@example.test"], markings=["family"])
    tags = _tag([_raw("dog", 0.9), _raw("cat", 0.8)])
    server = FastMCP("media")

    @server.tool()
    def media_tags() -> dict[str, object]:
        return {"records": media_tag_records(tags, access)}

    preset = ToolPreset(
        name="media-tags",
        server="media",
        tool="media_tags",
        id_field="id",
        records_path="records",
        params_style="args",
        params={},
        pagination="none",
    )
    adapter = McpToolSourceAdapter(
        preset, connector="media", mapping_reference="connector:media#mappings/MediaTag"
    )

    async def run():
        async with McpTransport().session(TransportEndpoint(in_process=server)) as s:
            await adapter.discover(s)
            return await adapter.extract(s, None)

    page = asyncio.run(run())
    assert [r.mapping_reference for r in page.records] == [
        "connector:media#mappings/MediaTag"
    ] * 2
    assert {r.record_id for r in page.records} == {tag_record_id(t) for t in tags}
    for record in page.records:
        assert record.payload["access"] == access.model_dump(mode="json")
        assert record.payload["taxonomy_id"] == "tax"
