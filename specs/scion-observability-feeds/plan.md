# SCION-OBSERVABILITY-FEEDS — Implementation plan

Governing spec: [spec.md](spec.md). Rows: [requirements.md](requirements.md).

## Summary

Delivery phase: P5

This plan covers the 10 active rows of SCION-OBSERVABILITY-FEEDS. Each row names its production caller chain, the files it edits and the existing helpers it reuses; the design below groups them. The SDK owns the connector side; the engine and the door own their sides by row ID.

## Technical Context

- **Language/Version**: Python 3.14 (floor 3.12)
- **Primary Dependencies**: pydantic, httpx, the generated Medulla client types and the MCP libraries pinned in pyproject.toml
- **Storage**: N/A (durable state lives in the engine; the SDK holds checkpoints and bounded caches)
- **Testing**: pytest with spec markers; each row names its test as path::name
- **Target Platform**: Linux containers and developer workstations
- **Project Type**: library with console scripts
- **Performance Goals**: bounded pages, retries and caches as each row states; no unbounded growth
- **Constraints**: no private data in the repository; engine contract read from the published package only
- **Scale/Scope**: 10 active rows

## Constitution Check

- R-I The SDK owns connector contracts and transports: pass. Each capability is implemented here once and connectors reuse it.
- R-II The public API is tested and reachable: pass. Every row names a test and a production caller.
- R-III The engine graph contract is external: pass. The contract is read from the published package.

## Project Structure

```text
scion_sdk/   existing package; each row names its files
tests/       existing tests; each row names path::name
specs/scion-observability-feeds/   existing spec files
```

## Phase 0: Research

no unknowns

## Phase 1: Design

1. Add a shared event-envelope validation layer using existing `SourceAdapter`, manifest, runner and generated-client sink contracts.
2. Implement RUM, security-audit and CI/CD provider adapters independently; keep vendor-specific parsing in their connector repositories.
3. Bind checkpoint/replay to durable receipts and add schema drift quarantine before submission.
4. Run offline fixtures, then opt-in contributor-owned feed and graph integration with redacted receipts.

### Rejected event page identity (R001.1)

Stage identity changes separately while validating the entire page. Merge them
only after the adapter forms a complete `RecordPage`. This guard leaves the
durable acceptance boundary open. A failed sink submission requires replay
semantics beyond the adapter-local tracker.

### Event identity receipt boundary (R001.2)

The worker promotes staged identity only after validating the matching sink receipt.
An adapter-local validation success is not an acceptance receipt. A failed or
cancelled submission discards the stage. Concurrent use of the same adapter
refuses while a page remains unfinished.

### Durable event receipt proof (R001.3)

An isolated graph accepts a synthetic page. A second worker process replays it.
Graph status retains the same receipt, and readback has one logical event.
The graph test skips if its optional server binary is absent.

### Event adapter registration (R001.4)

The live source declaration may name a feed kind. The sync runner loads that
binding through its composition root's activation policy and pins the live tool
contract before extraction. A missing or unauthorized policy refuses the feed.
The event tenant binding remains optional; this wiring does not establish a
trusted host tenant.

### Exact last-page replay (R001.6)

The accepted checkpoint binds a digest of the last raw event page and its adapter identity. A fresh adapter suppresses only the same page when the resulting checkpoint is also unchanged. Different pages with the same watermark still commit. This guard has constant size. It does not store all event identities. R001.3 proves a restart for one exact page. It does not prove all replay cases.

### Bounded identity index (R001.8)

One adapter retains at most 100,000 identity entries and 16 MiB of UTF-8
identity and digest bytes, including a candidate page. It refuses new entries
at either bound. Known duplicates and conflicts still resolve. This limit
does not bound provider response size. Reaching it stops new events until a
separate safe reset or durable identity strategy exists.

### Optional event tenant binding (R001.7)

The host may construct an adapter with an expected tenant. Each envelope must
match it before identity deduplication. The host owns tenant authentication.
The optional parameter does not bind the served runner or an unconfigured host.


## Phase 2: Task approach

tasks.md holds one task line per active row, ordered producer first; rows that share no file are marked parallel by their order.

## Complexity Tracking

none

## Build contract

Gates are the repository pre-commit and pre-push hooks (`uvx pre-commit run -c .config/pre-commit.yaml --hook-stage manual --all-files`): KISS limits from `.config/kiss.toml`, duplicate scans, type check, orphan-module and public-API-tested checks, and the spec hook. No scanner gaming and no duplicate implementation.

## Production caller chain

Console scripts and connector imports reach the owners below; each row's Caller chain names its own path.

- `scion_sdk/adapters/event_feed.py` (line 72): cited by 10 rows; its importers are named in each row's Caller chain.
- `scion_sdk/adapters/event_feed_bindings.py` (line 1): cited by 9 rows; its importers are named in each row's Caller chain.
- `scion_sdk/adapters/event_envelope.py` (line 35): cited by 8 rows; its importers are named in each row's Caller chain.
- `scion_sdk/adapters/event_errors.py` (line 24): cited by 4 rows; its importers are named in each row's Caller chain.
- `scion_sdk/discovery.py` (line 65): cited by 3 rows; its importers are named in each row's Caller chain.

## Cross-repo seams

none. Dependencies on other repositories are `Depends on:` rows that cite the owner row ID; this spec adds no new wire contract.
