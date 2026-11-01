# SCION-REPOSITORY-TRANSPORT — Authenticated immutable repository transport

**Requirement IDs:** SCION-REPOSITORY-TRANSPORT-R001, SCION-REPOSITORY-TRANSPORT-R002, SCION-REPOSITORY-TRANSPORT-R003, SCION-REPOSITORY-TRANSPORT-R004, SCION-REPOSITORY-TRANSPORT-R005.
**Owner:** scion-sdk. **Delivery state:** UNKNOWN. **Acceptance state:** OPEN.

## Outcome and boundary

The SDK fetches an authenticated immutable repository revision, pages bounded file blobs and tombstones, and submits them to the graph service's generated `IndexRepository` operation. The graph service parses symbols, resolves cross-file references, stores graph projections, and owns durable indexing receipts. The SDK never invents symbol or semantic identities. This enables a repository contributor to hydrate code and specification sources from their own Git provider.

## Contract

The current `repository/` package defines frozen `RepositoryRevision(provider, repository_id, revision_id, tree_id)`, `RepositoryAuthentication(provider, principal, mechanism, credential_reference_digest)`, `RepositoryFile(path, blob_digest, content)`, `RepositoryTombstone(path, prior_blob_digest, successor_path?)`, `RepositoryPage`, and `RepositoryBatchLimits`. Revision and tree IDs must be immutable Git object IDs; content digest is `sha256:<hex>` over bytes. Paths are logical repository-relative paths: no traversal, absolute path, duplicate path in a page, or machine-local location. Auth evidence is a digest of the credential reference, never the secret.

The provider is authenticated before fetching any content. `RepositorySnapshotProvider.fetch_page` must bind every returned page to the exact requested revision and tree. `repository/transport.py` validates pages, enforces a stable ordering and bounded cursor, and sends batches via `repository/indexing.py`. Default bounds are 4,096 files, 32 MiB total, 4 MiB per file, and 1,000 files per provider page; callers may lower them. A too-large file is refused with a typed outcome and remains retriable after a policy decision. No silently skipped blob may produce a complete receipt.

Changed blobs, deletions, and renames are diffed against the prior admitted revision. Tombstones preserve prior digest and optional successor path; unchanged blobs may be referenced by digest. Failed/unsupported parses must remain visible in the graph-owned result and eligible for retry on a later parser version or manual replay. A page/receipt is accepted only when requested and returned identity, path set, per-file outcomes, and digest all agree, so no blob is silently skipped and no batch can produce a complete indexing receipt when oversized or misaligned (SCION-REPOSITORY-TRANSPORT-R004). Cursor loops, missing outcomes, hash mismatch, auth downgrade, changed HEAD during walk, or cross-tenant reuse refuse before durable completion.

`repository/indexing.py` submits one batched `IndexRepository` call per bounded batch of files rather than issuing a separate round trip per file; the number of calls scales with the number of batches, not the number of files, eliminating a per-file request loop as the dominant network cost of a large-repository ingest (SCION-REPOSITORY-TRANSPORT-R005).

For performance (SCION-REPOSITORY-TRANSPORT-R003), traverse each distinct Git tree once and use batched object reads (`ls-tree -r` and `cat-file --batch` or equivalent provider APIs). Preserve exact output and refusal semantics. Benchmark on a public medium and large fixture with cold and warm cache, reporting clone/fetch/walk/submit separately, before and after. A speed claim without parity and reproducible measurement is not acceptance.

## Portable development

`uv sync` plus `uv run --frozen python -m pytest -q tests/test_repository*` exercises synthetic local Git fixtures and a fake generated client without a network. An optional GitHub fixture uses a public read-only repository and least-privilege token reference. A contributor may run the graph service locally for final receipts; no internal repository list is needed.

## Evidence and completion

| Gate | State | Evidence |
|---|---|---|
| Exact merged transport/source implementation | OPEN | Pending commit |
| Local immutable revision and reparse tests | OPEN | Pending CI run |
| Large-repo parity/performance and graph receipt | OPEN | Pending benchmark and redacted receipt |

**LANDED** means exact merged SDK code. **ACCEPTED** means the tests below pass and the measured transport plus graph receipt are attached. The existing repository package is a source slice, not a whole-program completion claim.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.

## User Scenarios & Testing

### User Story 1 - Authenticated immutable repository transport (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: The SDK fetches an authenticated immutable repository revision, pages bounded file blobs and tombstones, and submits them to the graph service's generated `IndexRepository` operation.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-REPOSITORY-TRANSPORT-R001, **When** it exercises "Repository transport hydrates code and specification sources from Git", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- Repository transport submits bounded, outcome-complete index batches: the row SCION-REPOSITORY-TRANSPORT-R004 states the refusal or bound and its test proves it.
- Provider pages respect their requested bound: the row SCION-REPOSITORY-TRANSPORT-R006 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: Repository transport hydrates code and specification sources from Git. Rows: SCION-REPOSITORY-TRANSPORT-R001.
- **FR-002**: SDK is the sole path for repository hydration. Rows: SCION-REPOSITORY-TRANSPORT-R002.
- **FR-003**: Repository ref walk traverses each Git tree once with batched reads. Rows: SCION-REPOSITORY-TRANSPORT-R003.
- **FR-004**: Repository transport submits bounded, outcome-complete index batches. Rows: SCION-REPOSITORY-TRANSPORT-R004.
- **FR-005**: Branch-aware transport batches indexing into one call per batch. Rows: SCION-REPOSITORY-TRANSPORT-R005.
- **FR-006**: Provider pages respect their requested bound. Rows: SCION-REPOSITORY-TRANSPORT-R006.
- **FR-007**: Local Git blob reads stay within the selected revision. Rows: SCION-REPOSITORY-TRANSPORT-R007.

## Success Criteria

- **SC-001**: Every active row is proven by its bound test and the pre-push gates pass. Rows: SCION-REPOSITORY-TRANSPORT-R001.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
