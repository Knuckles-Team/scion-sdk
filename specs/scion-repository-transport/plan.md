# SCION-REPOSITORY-TRANSPORT — Implementation plan

Governing spec: [spec.md](spec.md). Rows: [requirements.md](requirements.md).

## Summary

Delivery phase: P1

This plan covers the 7 active rows of SCION-REPOSITORY-TRANSPORT. Each row names its production caller chain, the files it edits and the existing helpers it reuses; the design below groups them. The SDK owns the connector side; the engine and the door own their sides by row ID.

## Technical Context

- **Language/Version**: Python 3.14 (floor 3.12)
- **Primary Dependencies**: pydantic, httpx, the generated Medulla client types and the MCP libraries pinned in pyproject.toml
- **Storage**: N/A (durable state lives in the engine; the SDK holds checkpoints and bounded caches)
- **Testing**: pytest with spec markers; each row names its test as path::name
- **Target Platform**: Linux containers and developer workstations
- **Project Type**: library with console scripts
- **Performance Goals**: bounded pages, retries and caches as each row states; no unbounded growth
- **Constraints**: no private data in the repository; engine contract read from the published package only
- **Scale/Scope**: 7 active rows

## Constitution Check

- R-I The SDK owns connector contracts and transports: pass. Each capability is implemented here once and connectors reuse it.
- R-II The public API is tested and reachable: pass. Every row names a test and a production caller.
- R-III The engine graph contract is external: pass. The contract is read from the published package.

## Project Structure

```text
scion_sdk/   existing package; each row names its files
tests/       existing tests; each row names path::name
specs/scion-repository-transport/   existing spec files
```

## Phase 0: Research

no unknowns

## Phase 1: Design

1. Preserve `repository/models.py`, `provider.py`, `transport.py`, `indexing.py` as the sole public transport path. Add provider adapters behind `RepositorySnapshotProvider`, not a second indexer.
2. Resolve a branch to immutable revision/tree once, authenticate, and bind every page and graph submission to that tuple. Fetch deterministic pages and validate paths/digests.
3. Diff against the last admitted revision, emit tombstones and changed files, maintain retryable failed/unsupported parse outcomes, and consume graph receipts before reporting completion.
4. Batch unique tree walks/object reads while preserving sorted output. Add reproducible benchmark and parity fixtures.
5. Cut consumers over to the SDK transport and graph-owned parsing; remove duplicate producer/consumer indexers after exact parity proof.

### Amendment 2026-10-27: bound provider pages

Count files and tombstones together. Reject a page above the requested size
before adding paths or submitting the page. Reject an
oversized tree page before collecting its entries. Earlier graph submissions
remain applied if a later page fails; this check does not provide rollback.

### Amendment 2026-10-27: bind local blob reads

The local Git provider validates that its requested commit is a commit object
and resolves to the declared tree. A blob read must name a blob in that tree.
The provider refuses another repository identity or an unrelated object.


## Phase 2: Task approach

tasks.md holds one task line per active row, ordered producer first; rows that share no file are marked parallel by their order.

## Complexity Tracking

none

## Build contract

Gates are the repository pre-commit and pre-push hooks (`uvx pre-commit run -c .config/pre-commit.yaml --hook-stage manual --all-files`): KISS limits from `.config/kiss.toml`, duplicate scans, type check, orphan-module and public-API-tested checks, and the spec hook. No scanner gaming and no duplicate implementation.

## Production caller chain

Console scripts and connector imports reach the owners below; each row's Caller chain names its own path.

- `scion_sdk/repository/__init__.py` (line 1): cited by 9 rows; its importers are named in each row's Caller chain.
- `scion_sdk/repository/indexing.py` (line 17): cited by 6 rows; its importers are named in each row's Caller chain.
- `scion_sdk/repository/local_git.py` (line 39): cited by 6 rows; its importers are named in each row's Caller chain.
- `scion_sdk/repository/errors.py` (line 6): cited by 6 rows; its importers are named in each row's Caller chain.
- `scion_sdk/testing/plugin.py` (line 27): cited by 2 rows; its importers are named in each row's Caller chain.

## Cross-repo seams

none. Dependencies on other repositories are `Depends on:` rows that cite the owner row ID; this spec adds no new wire contract.
