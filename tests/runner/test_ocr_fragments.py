"""Image records send OCR region fragments (SCION-SOURCE-INGEST-R908)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from scion_sdk.adapters.ocr_fragments import OcrExtractor, OcrRegion, image_fragments


class _Ocr:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, image: bytes, media_type: str) -> Sequence[OcrRegion]:
        self.calls += 1
        return [OcrRegion("hello", 1, 2, 30, 10), OcrRegion("  ", 0, 0, 1, 1)]


@pytest.mark.spec("SCION-SOURCE-INGEST-R908")
async def test_image_record_sends_region_fragments() -> None:
    ocr: OcrExtractor = _Ocr()
    (fragment,) = await image_fragments("scan-1", "image/png", b"\x89PNG", ocr)
    assert fragment["modality"] == "image" and fragment["text"] == "hello"
    assert fragment["span"] == {
        "ImageRegion": {"x": 1, "y": 2, "width": 30, "height": 10}
    }
    assert fragment["fragment_id"] == "scan-1#r0"
    assert await image_fragments("doc-1", "application/pdf", b"%PDF", ocr) == []
    assert ocr.calls == 1  # a non-image record never reaches the provider
