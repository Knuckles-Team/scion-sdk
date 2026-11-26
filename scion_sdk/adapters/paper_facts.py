"""Paper fact and citation-edge records for the source path (SCION-SOURCE-INGEST-R005.33).

The SDK transports source-attributed metadata only; the engine asserts the facts.
``paper_fact_records`` turns merged papers into one ``Paper`` row per revision and
one ``Citation`` row per edge, each bound to its field evidence.
"""

from __future__ import annotations

from typing import Any

from scion_sdk.adapters.paper_merge import PaperFactOutcome

__all__ = ["paper_fact_records"]


def paper_fact_records(outcome: PaperFactOutcome) -> list[dict[str, Any]]:
    """Rows for the merged papers; rejected papers contribute no row at all."""
    rows: list[dict[str, Any]] = []
    for paper in outcome.papers:
        revision = paper.revision or ""
        paper_id = f"{paper.canonical_id}@{revision}"
        rows.append(
            {
                "id": paper_id,
                "label": "Paper",
                "canonical_id": paper.canonical_id,
                "revision": revision,
                "title": paper.title or "",
                "unidentified": paper.canonical_id.startswith("unidentified:"),
                "edge_digest": paper.citation_edge_digest or "",
                "field_sources": {
                    name: [e.model_dump(mode="json") for e in evidence]
                    for name, evidence in sorted(paper.field_provenance.items())
                },
            }
        )
        rows.extend(
            {
                "id": f"{paper_id}->{edge.cited_canonical_id}",
                "label": "Citation",
                "canonical_id": paper.canonical_id,
                "cited": edge.cited_canonical_id,
                "title": "",
                "field_sources": {
                    "citation": [
                        e.model_dump(mode="json") for e in edge.source_evidence
                    ]
                },
            }
            for edge in paper.citation_edges
        )
    return rows
