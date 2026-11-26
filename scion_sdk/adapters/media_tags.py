"""Typed media tags from a declared producer (SC-R016).

A producer is a connector tool that declares the SDK contract. The SDK checks
bounds, validates every returned tag, orders and bounds the set, and gives each
tag a stable record id so an identical media item yields an identical set.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "CONTRACT_REVISION",
    "MEDIA_TAG_MAX_PER_ITEM",
    "MediaInput",
    "MediaTag",
    "MediaTagFailure",
    "MediaTagProducer",
    "MediaTagRefusal",
    "MediaTagRefusalCode",
    "tag_media",
    "tag_record_id",
]

MEDIA_TAG_MAX_PER_ITEM = 64
CONTRACT_REVISION = "media-tag-producer/1"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Modality = Literal["image", "thumbnail"]


class MediaTagRefusalCode(StrEnum):
    PRODUCER_UNKNOWN = "PRODUCER_UNKNOWN"
    MODALITY_UNSUPPORTED = "MODALITY_UNSUPPORTED"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    DIMENSIONS_EXCEEDED = "DIMENSIONS_EXCEEDED"
    TAG_UNAUTHORIZED = "TAG_UNAUTHORIZED"
    TAG_FABRICATED = "TAG_FABRICATED"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"
    PRODUCER_CAPABILITY_INSUFFICIENT = "PRODUCER_CAPABILITY_INSUFFICIENT"


class MediaTagFailure(BaseModel):
    """A visible failure record: the code and the input digest, nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: MediaTagRefusalCode
    input_digest: Digest


class MediaTagRefusal(Exception):
    """A closed refusal raised before any attachment or ingest."""

    def __init__(self, code: MediaTagRefusalCode, failure: MediaTagFailure) -> None:
        super().__init__(code.value)
        self.code = code
        self.failure = failure


class MediaTagProducer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    producer_id: str = Field(min_length=1)
    contract_revision: str = CONTRACT_REVISION
    producer_revision: str = Field(min_length=1)
    input_modalities: tuple[Modality, ...] = Field(min_length=1)
    max_bytes: int = Field(default=10_485_760, ge=1, le=52_428_800)
    max_dimension_px: int = Field(default=8192, ge=1, le=16_384)
    taxonomy_id: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    max_tags_per_item: int = Field(default=MEDIA_TAG_MAX_PER_ITEM, ge=1, le=256)


class MediaInput(BaseModel):
    """What the SDK knows about one media item; never its bytes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_item: str = Field(min_length=1)
    media_revision: str = Field(min_length=1)
    modality: str
    size_bytes: int = Field(ge=0)
    width_px: int = Field(ge=0)
    height_px: int = Field(ge=0)
    input_digest: Digest


class MediaTag(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    media_item: str = Field(min_length=1)
    media_revision: str = Field(min_length=1)
    taxonomy_id: str = Field(min_length=1)
    taxonomy_version: str = Field(min_length=1)
    object_or_context: Literal["object", "context"]
    label: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ref: str = Field(min_length=1)
    opaque_producer_id: str = Field(min_length=1)
    producer_revision: str = Field(min_length=1)
    provenance_digest: Digest
    origin: Literal["inferred", "extracted"]


Producer = Callable[[MediaInput], Awaitable[list[dict[str, object]]]]


def provenance_digest(
    media_digest: str, producer: MediaTagProducer, taxonomy: str, label: str, c: float
) -> str:
    body = json.dumps(
        [
            media_digest,
            producer.producer_id,
            producer.producer_revision,
            taxonomy,
            label,
            c,
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


def tag_record_id(tag: MediaTag) -> str:
    """Stable id: the same media, producer and label always give the same id."""
    body = "|".join(
        (tag.media_item, tag.opaque_producer_id, tag.producer_revision, tag.label)
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _refuse(code: MediaTagRefusalCode, media: MediaInput) -> MediaTagRefusal:
    return MediaTagRefusal(
        code, MediaTagFailure(code=code, input_digest=media.input_digest)
    )


def _check_input(producer: MediaTagProducer, media: MediaInput) -> None:
    if producer.contract_revision != CONTRACT_REVISION:
        raise _refuse(MediaTagRefusalCode.CONTRACT_MISMATCH, media)
    if media.modality not in producer.input_modalities:
        raise _refuse(MediaTagRefusalCode.MODALITY_UNSUPPORTED, media)
    if media.size_bytes > producer.max_bytes:
        raise _refuse(MediaTagRefusalCode.MEDIA_TOO_LARGE, media)
    if max(media.width_px, media.height_px) > producer.max_dimension_px:
        raise _refuse(MediaTagRefusalCode.DIMENSIONS_EXCEEDED, media)


def _validated(
    producer: MediaTagProducer, media: MediaInput, raw: dict[str, object]
) -> MediaTag:
    if (
        not raw.get("evidence_ref")
        or raw.get("opaque_producer_id") != producer.producer_id
    ):
        raise _refuse(MediaTagRefusalCode.TAG_FABRICATED, media)
    try:
        tag = MediaTag.model_validate(raw)
    except ValidationError as error:
        raise _refuse(MediaTagRefusalCode.TAG_FABRICATED, media) from error
    if (
        tag.media_item != media.media_item
        or tag.media_revision != media.media_revision
        or tag.producer_revision != producer.producer_revision
    ):
        raise _refuse(MediaTagRefusalCode.TAG_FABRICATED, media)
    if (tag.taxonomy_id, tag.taxonomy_version) != (
        producer.taxonomy_id,
        producer.taxonomy_version,
    ):
        raise _refuse(MediaTagRefusalCode.CONTRACT_MISMATCH, media)
    expected = provenance_digest(
        media.input_digest, producer, tag.taxonomy_id, tag.label, tag.confidence
    )
    if tag.provenance_digest != expected:
        raise _refuse(MediaTagRefusalCode.TAG_FABRICATED, media)
    return tag


async def tag_media(
    producer: MediaTagProducer,
    media: MediaInput,
    call: Producer,
    *,
    registered: frozenset[str],
) -> tuple[MediaTag, ...]:
    """Call the producer once; return the ordered, bounded, de-duplicated tag set."""
    if producer.producer_id not in registered:
        raise _refuse(MediaTagRefusalCode.PRODUCER_UNKNOWN, media)
    _check_input(producer, media)
    try:
        raws = await call(media)
    except PermissionError as error:
        raise _refuse(MediaTagRefusalCode.TAG_UNAUTHORIZED, media) from error
    except Exception as error:
        raise _refuse(
            MediaTagRefusalCode.PRODUCER_CAPABILITY_INSUFFICIENT, media
        ) from error
    unique = {
        tag_record_id(t): t for t in (_validated(producer, media, r) for r in raws)
    }
    kept = sorted(
        unique.values(), key=lambda t: (-t.confidence, t.taxonomy_id, t.label)
    )[: producer.max_tags_per_item]
    return tuple(sorted(kept, key=lambda t: (t.taxonomy_id, t.label)))
