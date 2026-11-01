# SCION-FINANCE-SOURCES — Implementation plan

Governing spec: [spec.md](spec.md). Rows: [requirements.md](requirements.md).

## Summary

Delivery phase: P5

This plan covers the 46 active rows of SCION-FINANCE-SOURCES. Each row names its production caller chain, the files it edits and the existing helpers it reuses; the design below groups them. The SDK owns the connector side; the engine and the door own their sides by row ID.

## Technical Context

- **Language/Version**: Python 3.14 (floor 3.12)
- **Primary Dependencies**: pydantic, httpx, the generated Medulla client types and the MCP libraries pinned in pyproject.toml
- **Storage**: N/A (durable state lives in the engine; the SDK holds checkpoints and bounded caches)
- **Testing**: pytest with spec markers; each row names its test as path::name
- **Target Platform**: Linux containers and developer workstations
- **Project Type**: library with console scripts
- **Performance Goals**: bounded pages, retries and caches as each row states; no unbounded growth
- **Constraints**: no private data in the repository; engine contract read from the published package only
- **Scale/Scope**: 46 active rows

## Constitution Check

- R-I The SDK owns connector contracts and transports: pass. Each capability is implemented here once and connectors reuse it.
- R-II The public API is tested and reachable: pass. Every row names a test and a production caller.
- R-III The engine graph contract is external: pass. The contract is read from the published package.

## Project Structure

```text
scion_sdk/   existing package; each row names its files
tests/       existing tests; each row names path::name
specs/scion-finance-sources/   existing spec files
```

## Phase 0: Research

no unknowns

## Phase 1: Design

1. Define shared normalized source-record/provenance model at the SDK adapter boundary and keep generated graph DTOs at the sink. Document explicit interval/session/adjustment semantics.
2. Build stock history/quote/stream fixture adapter with bounded complete pagination, corporate actions, news/calendar metadata and licensing checks.
3. Add macro/vintage, FX, spot/futures, and dated real-estate adapters by extending the same SourceAdapter and manifest presets.
4. Add account read and CSV mapping-preview/import with stable activity IDs and replay-safe checkpointing.
5. Run offline conformance first; then opt-in licensed live probes and graph receipt qualification. Release each adapter independently once its own evidence is complete.

### Canonical multi-asset MCP sources

R003.1 binds the MCP source to a tenant and source identity. It validates each
FX, commodity or real-estate record before submission. For property values, it
compares valuation age with the configured freshness threshold. Provider APIs
and licensed live access remain outside this evidence. R003.4 adds offline provider evidence in Emerald Exchange. It covers FMP FX, Databento fixed futures, Twelve Data gold spot and ATTOM valuations. The served pack test runs the SDK adapter and graph replay with graph 2.27.6. It uses the candidate SDK. It does not test live credentials or licensing. Explicitly bounded delta sources refuse reconciliation and authoritative pages.

### Cross-provider market source contract (R001.2)

The SDK validates bounded CCXT candles and CoinMarketCap records.
It also validates FRED vintage observations and sourced FOMC announcements.
All use one MCP source contract before sink submission. It accepts only delta pages
and refuses reconciliation because a bounded query is not an authoritative
live set. Emerald exchange history and Bellwether finance streams are the
producer paths. Live licensed access is separate evidence.

### Calendar identity collision refusal (R001.3)

The normalizer preserves legacy event IDs. It refuses a page when one ID maps
to different announcement instants, instead of returning conflicting records.
This does not establish cross-page identity or migrate existing graph records.

R001.9 validates the calendar page and its identity fields before returning
records. It normalizes announcement times to UTC. It does not implement a
provider-specific API.

### Payment amount validation

For SCION-FINANCE-SOURCES-R900.4, the guard refuses negative or non-finite
amounts and budget limits. Spend recording rejects invalid or overflowing
amounts before changing counters. Zero spend remains valid.

### Confirmed payment binding (R900.8)

The payment client records a confirmed payment only when the supplied proof
matches the challenge and the configured wallet. A mismatch changes no spend
counter or history. This comparison does not establish the transaction on chain.
### Independent bar intervals

For R900.7, market feed subscriptions use one aggregator per symbol and bar
interval. Each tick updates every interval for its symbol. Subscribers to the
same interval share the same aggregator.

### Account source binding

R004.1 requires the account snapshot, positions and CSV activity to pass through
an account-bound SDK source adapter before the sink. The adapter validates each
record and rejects a different account. It keeps the served provider fields and
does not invent a provider event time. It submits non-authoritative delta pages
and refuses reconciliation, so one account read cannot withdraw another. The owner pack test replays the same
account, positions and CSV through the adapter into a real graph.

### Stock source conformance evidence

The stock SDK tests cover each source kind and the supported adjustment and
session labels. Emerald Exchange tests replay served stock provider fixtures
through the SDK adapter into a real graph. Owner fixtures cover the websocket
stream and exchange calendar. A deterministic generated stock illustration is
available from the adapter, identified as generated and non-official. It does
not depend on a third-party logo license. Live provider access remains separate.
The SVG composition follows the universal-skills
[canvas-design skill](https://github.com/Knuckles-Team/universal-skills/blob/main/universal_skills/skills/canvas-design/SKILL.md).
The adapter renders it locally from the validated symbol. Runtime needs no model
or network. The image is not a company logo or provider fact.

### Point-in-time liquidity composition

R005.1 reads the three FRED series from the graph using tenant-scoped query.
It requires the source identity, exact value text and actual capture time.
The SDK converts the published decimal units into exact fixed-point inputs.
The graph owns the versioned composite and heat-strip calculation. A missing
capture, ambiguous source, nonfinite value or unsafe precision refuses before
compute. Capture time is not the provider's historical publication time.


## Phase 2: Task approach

tasks.md holds one task line per active row, ordered producer first; rows that share no file are marked parallel by their order.

## Complexity Tracking

none

## Build contract

Gates are the repository pre-commit and pre-push hooks (`uvx pre-commit run -c .config/pre-commit.yaml --hook-stage manual --all-files`): KISS limits from `.config/kiss.toml`, duplicate scans, type check, orphan-module and public-API-tested checks, and the spec hook. No scanner gaming and no duplicate implementation.

## Production caller chain

Console scripts and connector imports reach the owners below; each row's Caller chain names its own path.

- `scion_sdk/finance/__init__.py` (line 1): cited by 14 rows; its importers are named in each row's Caller chain.
- `scion_sdk/finance/fred_series.py` (line 27): cited by 10 rows; its importers are named in each row's Caller chain.
- `scion_sdk/finance/alpaca_account.py` (line 24): cited by 9 rows; its importers are named in each row's Caller chain.
- `scion_sdk/finance/csv_import.py` (line 43): cited by 9 rows; its importers are named in each row's Caller chain.
- `scion_sdk/adapters/mcp_tool.py` (line 56): cited by 8 rows; its importers are named in each row's Caller chain.

## Cross-repo seams

none. Dependencies on other repositories are `Depends on:` rows that cite the owner row ID; this spec adds no new wire contract.
