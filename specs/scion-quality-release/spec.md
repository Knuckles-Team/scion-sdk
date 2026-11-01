# SCION-QUALITY-RELEASE — Portable SDK quality, Pages, and release

**Requirement IDs:** SCION-QUALITY-RELEASE-R001, SCION-QUALITY-RELEASE-R002, SCION-QUALITY-RELEASE-R003, SCION-QUALITY-RELEASE-R004, SCION-QUALITY-RELEASE-R005.
**Owner:** scion-sdk. **Delivery state:** UNKNOWN. **Acceptance state:** OPEN.

## Outcome

An external contributor validates code and public docs from a fresh checkout with no running platform. The release job builds the SDK wheel against a compatible published graph-client wheel. It publishes guides through GitHub Pages. A checked-in `/docs` deployment copy is not required. Source docs may stay until one maintained Pages source replaces them. The source of truth is explicit, and generated output does not block unrelated code pull requests.

## Gate architecture

Separate two evidence classes. **Pull-request gates** use declared, lockable tools and synthetic fixtures: unit and contract pytest, Ruff, mypy, CCCC, KISS, Dupehound, jscpd, package wiring, dependency direction, public-doc link/privacy scan, build metadata, and wheel import smoke. They must have deterministic startup and no mandatory private socket, secret store, provider account, paid entitlement, external graph, registry publication, or protected branch permission. Missing required local tooling is provisioned from the repo lock or recorded as an infrastructure failure; it is never silently skipped.

**Release/acceptance gates** may use a contributor-owned environment: real graph client and connector receipts, strict Pages build/publication, online vulnerability audit, and artifact publication. These report `PASS`, `FAIL`, or `NOT RUN` separately from pull-request source status. A live acceptance failure blocks acceptance/release, not review of source changes. The release job must verify published wheel digest, installed consumer import and generated DTO compatibility; a local source overlay cannot pass it.

Public-doc privacy scanning searches Markdown, HTML source, examples and generated Pages inputs for private address ranges, host aliases, environment-only domains, credential strings, and machine paths. Test the scanner with planted violations to prove a red result. Synthetic RFC 5737/3849 addresses and `example.com` are allowed fixtures. Keep links relative or public. Pages navigation and SDK API docs must describe the exact released source, not a future design as current behavior.

## Fresh-checkout workflow

Install Python and `uv` as pinned in `pyproject.toml`; run `uv sync`, `uv run --frozen python -m pytest -q`, lint/type/wiring hooks, and `python -m build` or the repository's pinned wheel command (SCION-QUALITY-RELEASE-R003). The build must execute under that pinned interpreter rather than falling back to the hosting runner's system Python. The Pages source build runs in CI with its declared theme dependency. Contributors may run it locally using the same lock, and the resulting dependency import graph must reproduce the hosted continuous-integration run's recorded result exactly (SCION-QUALITY-RELEASE-R005); source PR validation never assumes a pre-existing sibling checkout.

The public README follows a fixed section order leading with a one-file connector quick start, and the published documentation site presents a task-first tutorial and architecture guide rather than an API-reference-first structure (SCION-QUALITY-RELEASE-R004).

For R005, the SDK installs `medulla` from PyPI only, never from source. `uv.lock` pins its registry wheels by SHA-256 and has no source distribution. `[tool.uv] no-build-package` forbids a source build, so the toolchain never compiles native code. `scripts/engine_release.json` names one locked wheel; the build job uses it for the consumer check. Bootstrap runs `uv sync --frozen`, then proves that the generated contracts import from the installed wheel. Graph inspection compares installed metadata with the lock. Every SDK-consumed Python contract module must resolve inside the installed wheel package. Include EG and each active transitive dependency in the graph. Traverse requested extras and fail closed on source, wheel, origin, metadata, dependency, or graph mismatch.

## Evidence and completion

| Gate | State | Evidence |
|---|---|---|
| Portable cloud PR gate | OPEN | Pending exact CI run |
| Planted privacy scanner violations | OPEN | Pending focused test |
| Published Pages and wheel consumer | OPEN | Pending URLs, artifact digest and release run |

The **LANDED** state requires the exact merge and docs source. The **ACCEPTED** state requires passing cloud PR tests from a fresh checkout and independent release evidence. A stale gate does not waive source correctness checks.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.

## User Scenarios & Testing

### User Story 1 - Portable SDK quality, Pages, and release (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: An external contributor validates code and public docs from a fresh checkout with no running platform.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-QUALITY-RELEASE-R001, **When** it exercises "SDK release builds against a published compatible graph-client wheel", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- SDK release builds against a published compatible graph-client wheel: the row SCION-QUALITY-RELEASE-R001 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: SDK release builds against a published compatible graph-client wheel. Rows: SCION-QUALITY-RELEASE-R001.
- **FR-002**: Public-documentation scanner catches private addresses and hosts. Rows: SCION-QUALITY-RELEASE-R002.
- **FR-003**: Release workflow builds with the pinned managed Python, not the system one. Rows: SCION-QUALITY-RELEASE-R003.
- **FR-004**: Public documentation follows a task-first README and site structure. Rows: SCION-QUALITY-RELEASE-R004.
- **FR-005**: Hosted CI reproduces the pinned dependency graph from published wheels. Rows: SCION-QUALITY-RELEASE-R005.
- **FR-006**: Metric scanners report weekly and never gate CI. Rows: SCION-QUALITY-RELEASE-R006.

## Success Criteria

- **SC-001**: Every active row is proven by its bound test and the pre-push gates pass. Rows: SCION-QUALITY-RELEASE-R001.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
