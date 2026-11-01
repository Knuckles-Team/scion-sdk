# SCION-FINANCE-SOURCES — Qualified finance source adapters

**Requirement IDs:** SCION-FINANCE-SOURCES-R001, SCION-FINANCE-SOURCES-R002, SCION-FINANCE-SOURCES-R003, SCION-FINANCE-SOURCES-R004, SCION-FINANCE-SOURCES-R005.
**Owner:** scion-sdk contract and source adapter APIs; each vendor connector owns its credentials and API implementation. **Delivery state:** SPECIFIED. **Acceptance state:** OPEN.

## Outcome

Finance consumers receive source data with instrument identity, entitlement, time basis, adjustment policy, completeness, and provenance explicit. The first vertical slice covers stocks and ETFs; later slices add FX, metals/commodities, real-estate valuations, and account import. The SDK supplies shared bounded pagination, credentials, transport, manifest presets, provenance and data-quality checks. Trading decisions, portfolio accounting, chart rendering, strategy evaluation and order execution belong to their owners.

## Canonical adapter records

All records include `tenant_id`, `source_id`, `provider`, `instrument_id`, `source_event_id`, `event_time_utc`, `observed_at_utc`, `schema_version`, `license_tag`, and `source_digest`. OHLCV bars additionally include `timeframe`, `session_calendar`, `currency`, `adjustment_mode`, and `completeness` (`COMPLETE`, `PARTIAL`, `GAP`). Quotes include bid/ask and quote timestamp. Corporate actions include ex-date/effective date, type and adjustment factor. Account activities include account identity, broker event ID or source-file digest + row identity, action, signed quantity, price, fees, currency and settlement time. Real-estate valuations include valuation date, method/source, currency, and staleness. These are SDK-side normalized source records; durable graph schemas and finance calculations are owned by the graph service. Generated graph-client DTOs must be used at the sink boundary rather than copied into this repository.

`SourceAdapter` discovery declares supported asset class, interval/date-range limits, session calendar, entitlement, rate limit, and adjustment semantics. It declares whether streams are historical or live. An unsupported interval, missing entitlement, invalid illustration input, absent extended-hours data, or incomplete page yields an explicit typed outcome. Pagination continues until the inclusive/exclusive interval is covered or a named incompleteness result is returned. Arbitrary first-50/first-365 truncation is prohibited. Historical and live events share identity and deduplication rules.

For stocks, a vendor adapter supports history, quotes and streams with split/dividend adjustments and regular and pre/post-market flags. It also supports profile, fundamentals, news, earnings and dividend calendars, and generated non-official illustrations (SCION-FINANCE-SOURCES-R002). Macro feeds such as FRED/ALFRED and a central-bank calendar preserve vintage and announcement time (SCION-FINANCE-SOURCES-R001). FX quotes identify base/quote and rate direction; gold/commodities distinguish spot from futures contract, expiry and explicit roll rule; real estate is a dated, potentially stale valuation rather than a live market quote (SCION-FINANCE-SOURCES-R003). Broker account/position reads and CSV activity import provide a mapping preview, reject ambiguous columns/currencies, and use stable provider event ID or file digest plus row ID so re-import is idempotent (SCION-FINANCE-SOURCES-R004).

The connector uses SDK `http/`, `credentials/`, `runner/`, `manifest/`, and `ports/source_adapter.py`; it never writes directly into graph storage. Credential references, entitlements and source licensing are validated before network calls. Provider outages, 429 retry-after, stale data, and partial pages preserve the last accepted checkpoint and report typed status to the caller.

Macro and liquidity panels provide Federal Reserve FRED series with preserved vintage, a versioned liquidity composite and heat-strip panel, ETF fund-flow data, sentiment data, and on-chain position data, each tagged with its source and observation time (SCION-FINANCE-SOURCES-R005).

## Portable development and evidence

Run `uv sync`; use deterministic recorded or synthetic responses with no paid key for offline conformance. A contributor can bring their own provider credential reference for live acceptance.

| Gate | State | Evidence |
|---|---|---|
| Exact merged adapter contracts | OPEN | Pending commit |
| Offline provider fixture conformance | OPEN | Pending CI run |
| Licensed live provider and graph receipt | OPEN | Pending redacted provenance/entitlement receipt |

**LANDED** requires exact merged source. **ACCEPTED** requires all test cases below with licensed source and graph receipt; no backtest or trade-performance claim follows from that acceptance.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.

## User Scenarios & Testing

### User Story 1 - Qualified finance source adapters (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: Finance consumers receive source data with instrument identity, entitlement, time basis, adjustment policy, completeness, and provenance explicit.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-FINANCE-SOURCES-R001, **When** it exercises "Market-data adapters: exchange history fix, CoinMarketCap, FRED/ALFRED", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- Refuse same-page calendar identity collisions: the row SCION-FINANCE-SOURCES-R001.3 states the refusal or bound and its test proves it.
- Alpaca account reads and idempotent CSV activity import: the row SCION-FINANCE-SOURCES-R004 states the refusal or bound and its test proves it.
- Reject duplicate CSV headers: the row SCION-FINANCE-SOURCES-R004.3 states the refusal or bound and its test proves it.
- Reject ragged CSV rows: the row SCION-FINANCE-SOURCES-R004.4 states the refusal or bound and its test proves it.
- Refusal: the row SCION-FINANCE-SOURCES-R900.4 states the refusal or bound and its test proves it.
- Bounded exchange history windows: the row SCION-FINANCE-SOURCES-R001.7 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: Market-data adapters: exchange history fix, CoinMarketCap, FRED/ALFRED. Rows: SCION-FINANCE-SOURCES-R001.
- **FR-002**: Alpaca stocks adapter: history, quotes, fundamentals, calendars. Rows: SCION-FINANCE-SOURCES-R002.
- **FR-003**: FX, commodities, and real-estate adapters with roll and staleness rules. Rows: SCION-FINANCE-SOURCES-R003.
- **FR-004**: Alpaca account reads and idempotent CSV activity import. Rows: SCION-FINANCE-SOURCES-R004.
- **FR-005**: Macro and liquidity panels: FRED series, ETF flows, sentiment, on-chain. Rows: SCION-FINANCE-SOURCES-R005.
- **FR-006**: Venue and API connector helpers (rollup). Rows: SCION-FINANCE-SOURCES-R900.
- **FR-007**: Probabilistic series forecast provider contract. Rows: SCION-FINANCE-SOURCES-R901.
- **FR-008**: Compute adapter binds quant analyses to NumericEvaluate. Rows: SCION-FINANCE-SOURCES-R902.
- **FR-009**: Strategy research record sink. Rows: SCION-FINANCE-SOURCES-R903.
- **FR-010**: Typed finance compute facade. Rows: SCION-FINANCE-SOURCES-R904.

## Success Criteria

- **SC-001**: Every active row is proven by its bound test and the pre-push gates pass. Rows: SCION-FINANCE-SOURCES-R001.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
