"""Deterministically merge provider metadata by explicit paper identity.

No provider call, quota decision, or document retrieval occurs here.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from scion_sdk.adapters.paper_identity import _groups, _identity_tokens
from scion_sdk.adapters.paper_models import (
    CitationEdge,
    DocumentOffer,
    FieldAlternative,
    FieldEvidence,
    PaperQuery,
    PaperRecord,
    PaperRejection,
    citation_edge_digest,
    revision_digest,
    unidentified_canonical_id,
)
from scion_sdk.adapters.paper_provider import ProviderPaper

__all__ = ["PaperFactOutcome", "extract_paper_facts", "merge_paper_records"]


def _json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _field(
    group: list[ProviderPaper], field: str
) -> tuple[Any, tuple[FieldEvidence, ...], tuple[FieldAlternative, ...]]:
    values: dict[str, tuple[Any, list[FieldEvidence]]] = {}
    provider_values: dict[str, str] = {}
    for record in group:
        value = getattr(record, field)
        if value in (None, "", ()):
            continue
        encoded = json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prior = provider_values.setdefault(record.reference.provider_id, encoded)
        if prior != encoded:
            raise ValueError("provider has conflicting field values")
        if encoded not in values:
            values[encoded] = (value, [])
        sources = values[encoded][1]
        for evidence in record.field_provenance[field]:
            if evidence not in sources:
                sources.append(evidence)
    if not values:
        return None, (), ()
    preferred, *others = values.values()
    alternatives = tuple(
        FieldAlternative(value=_json_value(value), evidence=tuple(sources))
        for value, sources in others
    )
    return preferred[0], tuple(preferred[1]), alternatives


def _canonical(group: list[ProviderPaper]) -> str:
    if any(item.unidentified for item in group):
        first = next(item for item in group if item.unidentified)
        return unidentified_canonical_id(first.title or "", first.authors)
    tokens = {token for item in group for token in _identity_tokens(item)}
    for prefix in ("doi:", "alias:", "provider:"):
        matches = sorted(token for token in tokens if token.startswith(prefix))
        if matches:
            return matches[0]
    raise ValueError("paper has no identity")


def _merge_group(group: list[ProviderPaper]) -> PaperRecord:
    fields = {
        name: _field(group, name)
        for name in ("title", "authors", "abstract", "publication_date")
    }
    references = tuple(dict.fromkeys(record.reference for record in group))
    offers = _public_offers(group)
    canonical_id = _canonical(group)
    edges = _merge_edges(group)
    return PaperRecord(
        canonical_id=canonical_id,
        revision=group[0].revision,
        citation_edges=edges,
        citation_edge_digest=citation_edge_digest(edges),
        revision_digest=revision_digest(canonical_id, group[0].revision),
        title=fields["title"][0],
        authors=fields["authors"][0] or (),
        abstract=fields["abstract"][0],
        publication_date=fields["publication_date"][0],
        document_offers=offers,
        references=references,
        field_provenance={
            name: result[1] for name, result in fields.items() if result[1]
        },
        alternate_values={
            name: result[2] for name, result in fields.items() if result[2]
        },
    )


def _merge_edges(group: list[ProviderPaper]) -> tuple[CitationEdge, ...]:
    """Union the provider edges by cited paper, keeping every source evidence."""
    evidence: dict[str, list[FieldEvidence]] = {}
    for record in group:
        for edge in record.citation_edges:
            sources = evidence.setdefault(edge.cited_canonical_id, [])
            for item in edge.source_evidence:
                if item not in sources:
                    sources.append(item)
    return tuple(
        CitationEdge(cited_canonical_id=cited, source_evidence=tuple(sources))
        for cited, sources in sorted(evidence.items())
    )


def _public_offers(group: list[ProviderPaper]) -> tuple[DocumentOffer, ...]:
    return tuple(
        dict.fromkeys(
            offer
            for record in group
            for offer in record.document_offers
            if not urlsplit(offer.url).query and not urlsplit(offer.url).fragment
        )
    )


def merge_paper_records(
    query: PaperQuery, records: tuple[ProviderPaper, ...]
) -> tuple[PaperRecord, ...]:
    """Merge one bounded batch in declared provider order.

    Unnamed providers, excessive pages, and conflicting duplicate provider IDs
    refuse before any output. Missing revisions never merge with known ones.
    """
    if len(records) > 10_000:
        raise ValueError("paper merge batch exceeds 10000 records")
    rank = {provider: index for index, provider in enumerate(query.provider_ids)}
    unique: dict[tuple[str, str, str | None], ProviderPaper] = {}
    counts = {provider: 0 for provider in query.provider_ids}
    for record in records:
        provider = record.reference.provider_id
        if provider not in rank:
            raise ValueError("paper names an undeclared provider")
        counts[provider] += 1
        if counts[provider] > query.page_bound:
            raise ValueError("provider page exceeds query bound")
        key = (provider, record.reference.record_id, record.revision)
        if key in unique and unique[key] != record:
            raise ValueError("provider identity has conflicting records")
        unique[key] = record
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda record: (
                rank[record.reference.provider_id],
                record.reference.record_id,
                record.revision or "",
            ),
        )
    )
    merged = tuple(_merge_group(group) for group in _groups(ordered))
    return tuple(
        sorted(merged, key=lambda record: (record.canonical_id, record.revision or ""))
    )


class PaperFactOutcome(NamedTuple):
    """Merged papers plus per-paper rejections; one refusal never changes another."""

    papers: tuple[PaperRecord, ...]
    rejections: tuple[PaperRejection, ...]


def _rejection(record: ProviderPaper, reason: str) -> PaperRejection:
    return PaperRejection(
        provider_id=record.reference.provider_id,
        record_id=record.reference.record_id,
        reason=reason,  # type: ignore[arg-type]
    )


def extract_paper_facts(
    query: PaperQuery, records: tuple[ProviderPaper, ...]
) -> PaperFactOutcome:
    """Merge the batch; when it refuses, merge per paper and reject only the culprits.

    A record that cannot stand alone is rejected as ``extraction_failed``; records
    that share a canonical id yet cannot merge are all rejected as
    ``conflicting_values``. Every other paper merges unchanged
    (SCION-SOURCE-INGEST-R005.32, R005.34).
    """
    try:
        return PaperFactOutcome(merge_paper_records(query, records), ())
    except ValueError:
        pass
    rejections: list[PaperRejection] = []
    singles: list[tuple[ProviderPaper, PaperRecord]] = []
    for record in records:
        try:
            (paper,) = merge_paper_records(query, (record,))
        except ValueError:
            rejections.append(_rejection(record, "extraction_failed"))
            continue
        singles.append((record, paper))
    owners: dict[str, list[ProviderPaper]] = {}
    for record, paper in singles:
        owners.setdefault(paper.canonical_id, []).append(record)
    papers: list[PaperRecord] = []
    for group in owners.values():
        try:
            papers.extend(merge_paper_records(query, tuple(group)))
        except ValueError:
            rejections.extend(_rejection(r, "conflicting_values") for r in group)
    papers.sort(key=lambda paper: (paper.canonical_id, paper.revision or ""))
    return PaperFactOutcome(tuple(papers), tuple(rejections))
