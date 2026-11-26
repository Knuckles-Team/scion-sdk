"""OCR text fragments for scanned image records.

SCION-SOURCE-INGEST-R908. For an ``image/*`` record the runner runs an
``OcrExtractor`` provider and sends the text as fragments with ``ImageRegion``
spans and modality ``image``. The engine holds no OCR engine.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = ["OcrExtractor", "OcrRegion", "image_fragments"]


@dataclass(frozen=True)
class OcrRegion:
    """One recognized text box in pixel coordinates."""

    text: str
    x: int
    y: int
    width: int
    height: int


class OcrExtractor(Protocol):
    """A provider that reads text from image bytes."""

    async def extract(self, image: bytes, media_type: str) -> Sequence[OcrRegion]: ...


async def image_fragments(
    record_id: str, media_type: str, data: bytes, extractor: OcrExtractor
) -> list[dict[str, Any]]:
    """Fragments of one record; a record that is not ``image/*`` yields none."""
    if not media_type.startswith("image/"):
        return []
    regions = await extractor.extract(data, media_type)
    return [
        {
            "fragment_id": f"{record_id}#r{index}",
            "text": region.text,
            "modality": "image",
            "span": {
                "ImageRegion": {
                    "x": region.x,
                    "y": region.y,
                    "width": region.width,
                    "height": region.height,
                }
            },
        }
        for index, region in enumerate(regions)
        if region.text.strip()
    ]
