# SCION-OBSERVABILITY-FEEDS — Operational event feeds through the source adapter contract

**Requirement ID:** SCION-OBSERVABILITY-FEEDS-R001. **Owner:** scion-sdk contract; each event connector owns provider transport. **Delivery state:** SPECIFIED. **Acceptance state:** OPEN.

## Outcome and model

Real-user monitoring (RUM), security audit, and CI/CD events can enter the same durable source pipeline as other systems of record. The SDK provides authenticated transport, bounded paging/streaming, stable event identity, manifests, checkpointing, and schema drift quarantine. It does not interpret an event as a verified incident, vulnerability, release, or user outcome. Graph-side policy and analysis own those conclusions.

An event envelope carries tenant/source/stream IDs; provider event ID or canonical source digest; event kind and schema version; event time, source-observed time and ingest time; actor and target references; trace/correlation ID if supplied; visibility/sensitivity label; license/retention metadata; payload digest; and source provenance. RUM captures page/action/error/session signals without raw credential or unapproved personal data. Security audit captures principal, action, target, outcome, and audit-chain reference. CI/CD captures repository revision, job/run/attempt identity, status transitions, artifact digest and deployment environment label. Missing required identity or impossible time order is refused, not patched with current time.

`SourceAdapter.discover` checks event API schema, scopes, pagination and retention window. `extract` returns a bounded page after the graph-owned checkpoint. Webhooks may enqueue hints, but durable source reconciliation controls completion. A repeated event ID with the same digest is idempotent; same ID with changed payload is a version/conflict, not a silent overwrite. Deletions and retention expiry use explicit tombstone/expiry outcomes. Drift quarantine prevents checkpoint advance. Backpressure bounds memory, concurrency and retry; 429 and outages surface typed lag, not fabricated success.

## Portable contribution and completion

Use synthetic RUM, audit and CI event fixtures with fake sessions and generated-client ports after `uv sync`. No production telemetry, private fleet, or hosted service is necessary for source tests. **LANDED** requires exact merged adapter contract and one provider implementation per feed kind. **ACCEPTED** requires the tests below, a graph-backed receipt and restart replay. No source implementation or live evidence is claimed by this spec.

## User Scenarios & Testing

### User Story 1 - Operational event feeds through the source adapter contract (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: Real-user monitoring (RUM), security audit, and CI/CD events can enter the same durable source pipeline as other systems of record.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-OBSERVABILITY-FEEDS-R001, **When** it exercises "RUM, security-audit, and CI/CD events join the durable source pipeline", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- Rejected page identity isolation: the row SCION-OBSERVABILITY-FEEDS-R001.1 states the refusal or bound and its test proves it.
- Event identity receipt boundary: the row SCION-OBSERVABILITY-FEEDS-R001.2 states the refusal or bound and its test proves it.
- Bound the adapter identity index: the row SCION-OBSERVABILITY-FEEDS-R001.8 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: RUM, security-audit, and CI/CD events join the durable source pipeline. Rows: SCION-OBSERVABILITY-FEEDS-R001.
- **FR-002**: Host profile feed. Rows: SCION-OBSERVABILITY-FEEDS-R002.

## Success Criteria

- **SC-001**: Every active row is proven by its bound test and the pre-push gates pass. Rows: SCION-OBSERVABILITY-FEEDS-R001.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
