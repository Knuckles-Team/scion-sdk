"""Paper contracts bind identities, continuations and deterministic evidence."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from live_engine import live_ingest, scoped_client  # noqa: F401
from pydantic import ValidationError

from scion_sdk.adapters.paper_download import (
    DocumentAdmission,
    DocumentRefusal,
    DocumentRequest,
    PaperDocumentService,
)
from scion_sdk.adapters.paper_merge import merge_paper_records
from scion_sdk.adapters.paper_models import (
    DocumentOffer,
    DocumentReceipt,
    FieldAlternative,
    FieldEvidence,
    PaperContinuation,
    PaperQuery,
    PaperRecord,
    PaperSearchResult,
    ProviderReference,
    ProviderReport,
)
from scion_sdk.adapters.paper_provider import (
    ProviderFailure,
    ProviderPage,
    ProviderPaper,
)
from scion_sdk.adapters.paper_quota import PaperQuotaPolicy, SharedPaperQuota
from scion_sdk.adapters.paper_search import PaperSearchService, ProviderBinding
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.http.retry import RetryPolicy
from scion_sdk.testing.http_server import ScriptedHttpServer, ScriptedResponse


def _query(**changes):
    values = {
        "tenant": "tenant",
        "query": "source systems",
        "provider_ids": ("first", "second"),
        "schema_revision": "v1",
    }
    return PaperQuery(**(values | changes))


def _paper(provider, record, *, title="Title", doi=None, revision="v1", alias=None):
    evidence = FieldEvidence(provider_id=provider, source_id=record, location="title")
    return ProviderPaper(
        reference=ProviderReference(provider_id=provider, record_id=record),
        doi=doi,
        canonical_alias=alias,
        revision=revision,
        title=title,
        field_provenance={"title": (evidence,)},
    )


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.1")
def test_query_continuations_bind_exact_request_and_limits():
    query = _query()
    token = PaperContinuation(
        provider_id="first",
        query_sha256=query.query_digest(),
        provider_ids=query.provider_ids,
        schema_revision="v1",
        cursor="next",
    )
    resumed = _query(continuation={"first": token})
    assert PaperQuery.model_validate_json(resumed.model_dump_json()) == resumed
    with pytest.raises(ValidationError):
        _query(query="different", continuation={"first": token})
    with pytest.raises(ValidationError):
        _query(provider_ids=("first", "first"))
    with pytest.raises(ValidationError):
        _query(deadline_seconds=float("nan"))
    with pytest.raises(ValidationError):
        _query(page_bound=0)


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.1")
def test_provider_outcomes_and_document_receipts_are_explicit():
    source = FieldEvidence(provider_id="first", source_id="1", location="title")
    offer = DocumentOffer(
        provider_id="first",
        url="https://example.org/a.pdf",
        media_type="application/pdf",
    )
    paper = PaperRecord(
        canonical_id="provider:first:1",
        title="Title",
        references=(ProviderReference(provider_id="first", record_id="1"),),
        field_provenance={"title": (source,)},
        document_offers=(offer,),
        publication_date=date(2026, 1, 1),
        alternate_values={
            "title": (FieldAlternative(value="Other", evidence=(source,)),)
        },
    )
    success = ProviderReport(provider_id="first", outcome="ok")
    failed = ProviderReport(
        provider_id="second", outcome="failed", reason="provider_failed"
    )
    result = PaperSearchResult(
        records=(paper,), reports=(success, failed), completeness="partial"
    )
    assert PaperSearchResult.model_validate_json(result.model_dump_json()) == result
    assert PaperSearchResult(reports=(success,), completeness="complete").records == ()
    with pytest.raises(ValidationError):
        PaperSearchResult(records=(paper,), reports=(failed,), completeness="failed")
    with pytest.raises(ValidationError):
        ProviderReport(provider_id="first", outcome="failed")
    receipt = DocumentReceipt(
        tenant="tenant",
        paper_id="paper",
        revision="v1",
        source_url="https://example.org/a.pdf",
        media_type="application/pdf",
        byte_count=10,
        sha256="a" * 64,
        artifact_ref="sha256/aa/item",
    )
    assert receipt.byte_count == 10
    for change in (
        {"artifact_ref": "../outside"},
        {"artifact_ref": "/absolute"},
        {"source_url": "https://example.org/a?token=example"},
        {"sha256": "bad"},
    ):
        with pytest.raises(ValidationError):
            DocumentReceipt.model_validate(receipt.model_dump() | change)


def test_merge_is_order_independent_and_keeps_conflicting_sources():
    first = _paper("first", "1", title="First title", doi="https://doi.org/10.1234/AbC")
    second = _paper("second", "x", title="Second title", doi="doi:10.1234/abc")
    (merged,) = merge_paper_records(_query(), (second, first))
    assert merged == merge_paper_records(_query(), (first, second))[0]
    assert merged.canonical_id == "doi:10.1234/abc" and merged.title == "First title"
    assert merged.field_provenance["title"][0].provider_id == "first"
    assert merged.alternate_values["title"][0].value == "Second title"
    assert merged.alternate_values["title"][0].evidence[0].provider_id == "second"
    assert [reference.provider_id for reference in merged.references] == [
        "first",
        "second",
    ]


def test_merge_never_guesses_identity_or_known_revisions():
    a = _paper("first", "a", title="Same title")
    b = _paper("second", "b", title="Same title")
    assert len(merge_paper_records(_query(), (a, b))) == 2
    known = _paper("first", "a", doi="10.1234/x", revision="v1")
    unknown = _paper("second", "b", doi="10.1234/x", revision=None)
    assert len(merge_paper_records(_query(), (known, unknown))) == 2
    linked = _paper("first", "a", doi="10.1234/x", alias="stable")
    alias = _paper("second", "b", alias="stable")
    assert len(merge_paper_records(_query(), (linked, alias))) == 1
    conflict = _paper("second", "b", doi="10.1234/y", alias="stable")
    with pytest.raises(ValueError, match="conflicting DOI"):
        merge_paper_records(_query(), (linked, conflict))
    with pytest.raises(ValueError, match="undeclared provider"):
        merge_paper_records(_query(), (_paper("other", "1"),))
    with pytest.raises(ValueError, match="conflicting records"):
        merge_paper_records(_query(), (a, _paper("first", "a", title="Changed")))


def test_same_provider_conflicts_refuse_without_invented_precedence():
    first = _paper("first", "1", title="First", doi="10.1234/a")
    other = _paper("first", "2", title="Other", doi="10.1234/a")
    with pytest.raises(ValueError, match="conflicting field"):
        merge_paper_records(_query(), (first, other))


def test_merge_omits_signed_offers_and_redacts_provider_reference():
    record = _paper("first", "a").model_copy(
        update={
            "reference": ProviderReference(
                provider_id="first",
                record_id="a",
                original_id="https://example.test/paper?token=secret",
            ),
            "document_offers": (
                DocumentOffer(
                    provider_id="first",
                    url="https://example.test/file?signature=secret",
                    media_type="application/pdf",
                ),
                DocumentOffer(
                    provider_id="first",
                    url="https://example.test/public.pdf",
                    media_type="application/pdf",
                ),
            ),
        }
    )
    (merged,) = merge_paper_records(_query(), (record,))
    assert merged.references[0].original_id == "https://example.test/paper"
    assert [offer.url for offer in merged.document_offers] == [
        "https://example.test/public.pdf"
    ]
    assert "secret" not in merged.model_dump_json()


class _Fixed:
    """A provider that returns canned papers, or fails when given none."""

    def __init__(self, name, papers=None):
        self.name, self.papers = name, papers

    async def search(self, query, cursor):
        if self.papers is None:
            raise ProviderFailure("provider_failed")
        return ProviderPage(records=self.papers)


def _policy(name, group):
    return PaperQuotaPolicy(
        provider_id=name,
        credential_group=group,
        policy_revision="v1",
        minimum_spacing_ns=1,
    )


async def _allow(query, ctx):
    return query.tenant == "tenant"


async def _search_and_download(engine, url, root):
    quota = SharedPaperQuota(engine)
    names = ("first", "second", "broken")
    bindings = {
        "first": ProviderBinding(
            _Fixed("first", (_paper("first", "1", doi="10.1234/AbC"),)),
            _policy("first", "g-first"),
            retries=0,
        ),
        "second": ProviderBinding(
            _Fixed(
                "second", (_paper("second", "x", title="Other", doi="doi:10.1234/abc"),)
            ),
            _policy("second", "g-second"),
            retries=0,
        ),
        "broken": ProviderBinding(
            _Fixed("broken"), _policy("broken", "g-broken"), retries=0
        ),
    }
    service = PaperSearchService(bindings, quota=quota, authorize=_allow)
    result = await service.search(_query(provider_ids=names), object())
    with pytest.raises(ValueError):
        PaperSearchService(bindings, quota=quota, authorize=_allow, max_providers=33)
    narrow = PaperSearchService(
        bindings, quota=quota, authorize=_allow, max_providers=1
    )
    refused = await narrow.search(_query(provider_ids=names), object())

    doc_policy = _policy("arxiv", "g-doc")

    async def admit(request, ctx):
        return DocumentAdmission(
            root=Path(root),
            http=HttpClientOptions(
                base_url=url,
                pin_egress=True,
                allow_loopback=True,
                retry=RetryPolicy(max_attempts=1),
            ),
            quota=doc_policy,
        )

    downloads = PaperDocumentService(quota, admit)
    request = DocumentRequest(
        tenant="tenant",
        paper_id="doi:10.1234/abc",
        revision="v1",
        offer=DocumentOffer(
            provider_id="arxiv", url=url + "/paper?token=x", media_type="text/html"
        ),
    )
    receipt = await downloads.download(request, object())
    async with quota.request_slot(doc_policy):
        with pytest.raises(DocumentRefusal) as busy:
            await downloads.download(request, object())
    return result, refused, receipt, busy.value.reason


async def _with_engine(url, root):
    engine = await scoped_client(
        ["kv:read", "kv:write", "kv:shared:read", "kv:shared:write"]
    )
    try:
        return await _search_and_download(engine, url, root)
    finally:
        await engine.close()


@pytest.mark.spec("SCION-SOURCE-INGEST-R005.2")
def test_dedup_partial_failure_quota_download_and_metadata_fallback(
    live_ingest,  # noqa: F811
    tmp_path,
):
    from scion_sdk.adapters.paper_document_metadata import apply_document_fallback

    body = (
        b'<html><head><title>Doc</title><meta name="author" content="Jane">'
        b"</head><body>Research</body></html>"
    )
    with ScriptedHttpServer(
        ScriptedResponse(body=body, headers={"Content-Type": "text/html"})
    ) as server:
        result, refused, receipt, busy = asyncio.run_coroutine_threadsafe(
            _with_engine(server.base_url, tmp_path),
            live_ingest._loop,
        ).result(30)
        assert len(server.requests) == 1
    assert result.completeness == "partial"
    assert [r.outcome for r in result.reports] == ["ok", "ok", "failed"]
    (merged,) = result.records
    assert merged.canonical_id == "doi:10.1234/abc"
    assert {ref.provider_id for ref in merged.references} == {"first", "second"}
    assert refused.completeness == "failed"
    assert {r.reason for r in refused.reports} == {"invalid_query"}
    assert busy == "rate_limited"
    assert "token" not in receipt.source_url
    assert (tmp_path / receipt.artifact_ref).read_bytes() == body
    assert [p.name for p in tmp_path.iterdir()] == [receipt.artifact_ref]
    enriched = apply_document_fallback(merged, receipt, tmp_path)
    assert enriched.title == merged.title and enriched.authors == ("Jane",)
    assert enriched.field_provenance["authors"][0].document_sha256 == receipt.sha256
    for bad in (
        {"page_bound": 1001},
        {"deadline_seconds": 601},
        {"deadline_seconds": 0.5},
    ):
        with pytest.raises(ValidationError):
            _query(**bad)
    for bad_retries in (-1, 6):
        with pytest.raises(ValueError):
            ProviderBinding(
                _Fixed("first", ()), _policy("first", "g"), retries=bad_retries
            )
    assert json.dumps(result.model_dump(mode="json"))
