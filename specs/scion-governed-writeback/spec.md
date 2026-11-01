# SCION-GOVERNED-WRITEBACK — Governed write-back to attached sources

**Requirement IDs:** SCION-GOVERNED-WRITEBACK-R001, SCION-GOVERNED-WRITEBACK-R002, SCION-GOVERNED-WRITEBACK-R003, SCION-GOVERNED-WRITEBACK-R004, SCION-GOVERNED-WRITEBACK-R005.
**Owner:** scion-sdk source-side protocol.

## Outcome and boundary

A tenant can propose a scoped change to an attached source, see the exact dry-run, approve it, apply it once, and reconcile an uncertain outcome after process restart. A durable graph service owns the canonical `SourceChangeSet`, authorization and append-only `WriteBackAttempt`/reconciliation receipts. The SDK owns source-side verification and transport. A vendor adapter owns the actual app API call. If an application exposes business rules through its API, the adapter uses that API; direct database writes are not an interchangeable shortcut.

## Required protocol

The generated `SourceChangeSet` binds tenant, connector, source, change-set ID/digest, idempotency key, base source version, exact field scope, desired patch, field provenance, expiry, input/output contract digests, and authorization claim. `WriteBackPort` exposes `current_version`, `dry_run`, `apply`, and `reconcile`. `DurableWritableConnector` composes `WriteBackTransport` with a durable `WriteBackLedger` and must be reused. There is one source mutation attempt authority; no parallel connector-local approval database may replace graph receipts.

1. Register and read the exact canonical change set from the `WriteBackLedger` before every operation. Reject a missing, changed, expired, wrong-tenant, or wrong-connector record. Dry-run reads current version and returns a digest-bound preview without mutation.
2. Before apply, check optimistic source version, exact field scope and provenance, contract pin, deterministic authorization mode, approval bound to the same digest, and an audit reservation. A decision-system proposal is never approval. A denial or stale version makes zero source calls.
3. A successful application records a durable attempt and source effect identity before a caller can report completion. Repeating the same idempotency key returns the proven prior effect. Reusing the key with different digest refuses.
4. On timeout or ambiguous result, record `OUTCOME_UNCERTAIN` and stop automatic retries. After restart, replay durable receipts. Only source reconciliation proving `NO_EFFECT` permits another attempt; `APPLIED` returns the original effect and unresolved status remains closed to retry.
5. The adapter must preserve tenant and source principal isolation, redact credentials and sensitive field values from traces, and respect vendor API rate limits. A user-visible receipt contains effect status, exact target and version, approval identity/digest, and provenance digest, not raw secrets.

## Fresh-checkout development

Run `uv sync` and focused `uv run --frozen python -m pytest -q tests/test_writeback*`. Fakes implement `WriteBackTransport` and `WriteBackLedger` with controllable failures; unit and restart tests require no live app. A contributor-owned test app and graph service are used for final cross-process acceptance, with secrets supplied as references. The spec can be implemented without any private service inventory.

## Evidence and completion

The current SDK modules implement generic source-side checks and durable receipt handling. They do not establish a production repository adapter, real provider conditional mutation, independent revision readback or fleet acceptance. Required evidence includes exact merged revisions, restart results and adversarial test results. A contributor-owned writable adapter journey must produce redacted cross-process receipts. This definition change has not run these repository scenarios.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.


## Wallet proof producer

SCION-GOVERNED-WRITEBACK-R001.1 through R001.6 define the signature-verification producer consumed by ARBORA-DATA-MARKET-R007.
The SDK produces source-side cryptographic verification facts; EG owns authorization decisions and durable replay/effect records.
Arbora authenticates the caller and serves explicit confirmation. A signature never replaces either approval or account entitlement.
Unavailable verifier, key trust or durable binding keeps wallet operations unavailable.

Confirmation binds the complete immutable `{"intent": WalletBusinessIntent}` projection defined in plan.md.
Proof and confirmation metadata use the separate, currently missing WalletExecutionEnvelope transport extension.
No proof or derived plan digest enters the confirmed business parameters. The proof transport wiring has not been implemented.


## Repository source-effect producer

SCION-GOVERNED-WRITEBACK-R004.1 through R004.4 retain the full generic parent obligations for all attached sources. R004.5 through R004.12 define a regular-file create/update profile without narrowing those obligations. All children and the original parent acceptance are required for the rollup.

The SDK validates the exact approved repository effect and delegates conditional mutation and independent readback to an admitted provider adapter. It reuses the existing graph-owned change-set, authorization, audit and durable attempt authorities. Read-only source ingestion, hydrated files and graph prompt exports are inputs, never write permission. Arbora composes the consumer under ARBORA-FLEET-R003 and existing ARBORA-OPS-R009; MEDULLA-TYPED-PACKS-R094.7 consumes the resulting evidence without becoming a filesystem writer. Those cross-repository consumer definitions and implementations are separate prerequisites, not supplied by the ordinary SDK apply seam.

The proposed request, intent and receipt names in plan.md are contracts to implement, not existing exported classes. This definition adds no connector, credentials, permissions or runtime code. No research derivation is claimed.

## User Scenarios & Testing

### User Story 1 - Governed write-back to attached sources (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: A tenant can propose a scoped change to an attached source, see the exact dry-run, approve it, apply it once, and reconcile an uncertain outcome after process restart.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-GOVERNED-WRITEBACK-R001, **When** it exercises "Write-back proposals route through governed authorization (rollup)", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- Refusal: the row SCION-GOVERNED-WRITEBACK-R001.4 states the refusal or bound and its test proves it.
- Governed write-back approval, idempotency and audit reservation (rollup): the row SCION-GOVERNED-WRITEBACK-R004 states the refusal or bound and its test proves it.
- Reject invalid audit reservation identifiers: the row SCION-GOVERNED-WRITEBACK-R004.13 states the refusal or bound and its test proves it.
- Refuse overlapping effects in one connector: the row SCION-GOVERNED-WRITEBACK-R004.17 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: Write-back proposals route through governed authorization (rollup). Rows: SCION-GOVERNED-WRITEBACK-R001.
- **FR-002**: Source-side WriteBackPort composes a durable graph write-back adapter. Rows: SCION-GOVERNED-WRITEBACK-R002.
- **FR-003**: Full write-back requires an authorization resolver and restart recovery. Rows: SCION-GOVERNED-WRITEBACK-R003.
- **FR-004**: Governed write-back approval, idempotency and audit reservation (rollup). Rows: SCION-GOVERNED-WRITEBACK-R004.
- **FR-005**: Write-back implementation passes its full durable test suite together. Rows: SCION-GOVERNED-WRITEBACK-R005.
- **FR-006**: Execute a released governed change set. Rows: SCION-GOVERNED-WRITEBACK-R006.

## Success Criteria

- **SC-001**: Every active row is proven by its bound test and the pre-push gates pass. Rows: SCION-GOVERNED-WRITEBACK-R001.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
