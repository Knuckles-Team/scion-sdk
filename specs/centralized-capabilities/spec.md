# SCION-CENTRALIZED-CAPABILITIES — Centralized connector capabilities

Owner: scion-sdk. Cross-repo IDs: none.
Public provenance: operator request of 2026-10-22 to track everything the SDK centralizes for connectors.

## Purpose and user stories

A connector author must know which capabilities the SDK provides and which code the connector must not write. A reviewer must find the owning requirement for each capability in one place. This spec is that index. The index lists 40 capabilities. Each has an owner row, a module or extra, a rule for connectors, and a conformance method.

## Requirements and acceptance

- FR-001: Each row `SCION-CENTRALIZED-CAPABILITIES-R001` to `SCION-CENTRALIZED-CAPABILITIES-R040` says connectors use one SDK capability and ship no copy.
- FR-002: The index in `plan.md` names the owner row, the module or extra, and the conformance method of each row. A missing method reads "unchecked". Rows: `SCION-CENTRALIZED-CAPABILITIES-R001`.
- SC-001: Every module path in the index exists in the package, and every owner row ID exists in a `requirements.md` file. Rows: `SCION-CENTRALIZED-CAPABILITIES-R001`.
- SC-002: Every key in `[project.optional-dependencies]` appears in at least one row. Rows: `SCION-CENTRALIZED-CAPABILITIES-R040`.
- FR-003: Connectors read and write object stores and file shares through one SDK backend protocol and its five backends. Rows: `SCION-CENTRALIZED-CAPABILITIES-R042`, `SCION-CENTRALIZED-CAPABILITIES-R042.1`, `SCION-CENTRALIZED-CAPABILITIES-R042.2`, `SCION-CENTRALIZED-CAPABILITIES-R042.3`, `SCION-CENTRALIZED-CAPABILITIES-R042.4`, `SCION-CENTRALIZED-CAPABILITIES-R042.5`.
- FR-004: Store credentials arrive by secret reference only, and every storage endpoint passes the egress guard. Rows: `SCION-CENTRALIZED-CAPABILITIES-R042.6`, `SCION-CENTRALIZED-CAPABILITIES-R042.7`.
- FR-005: A tool that produces files takes inputs from sinks or URLs and publishes outputs to every configured sink. Rows: `SCION-CENTRALIZED-CAPABILITIES-R043`, `SCION-CENTRALIZED-CAPABILITIES-R043.1`, `SCION-CENTRALIZED-CAPABILITIES-R043.2`, `SCION-CENTRALIZED-CAPABILITIES-R043.3`, `SCION-CENTRALIZED-CAPABILITIES-R043.4`.
- FR-006: The tool result links each published output, and the engine may record its lineage. Rows: `SCION-CENTRALIZED-CAPABILITIES-R043.5`, `SCION-CENTRALIZED-CAPABILITIES-R043.6`.
- SC-003: A served upstream call with one input reference returns one link, and both test sinks hold the output. Rows: `SCION-CENTRALIZED-CAPABILITIES-R042.8`, `SCION-CENTRALIZED-CAPABILITIES-R043.7`.

## Why a new spec

Search: `grep -rn` over `specs/` for each module and capability term. The specs read were `SCION-CONNECTOR-CONTROL`, `SCION-SOURCE-INGEST`, `SCION-FINANCE-SOURCES`, `SCION-GOVERNED-WRITEBACK`, `SCION-SCM`, `SCION-REPOSITORY-TRANSPORT`, and `SCION-OBSERVABILITY-FEEDS`. Each owns the contract of its capability. No spec lists the capabilities together or states the rule that connectors reuse them. Five capabilities had no owner: pagination, rate limiting, error redaction, the extras rule, and the earth-observation records. This spec owns those rows. It extends no existing row. It adds no contract that an owner row already states.

## Scope and interfaces

Owned behavior: the connector-facing rule for each capability. The owner rows keep the contracts. Dependencies: the owner rows listed in `plan.md`. GraphQL builds on `SCION-CONNECTOR-CONTROL-R014.12`, which another lane extends. Connector identity is `SCION-CONNECTOR-CONTROL-R071`.

Non-goals: a fleet-wide scan. Row `SCION-CENTRALIZED-CAPABILITIES-R025` adds the `vector` and `vector-chroma` extras for vector-store clients. Row `SCION-CENTRALIZED-CAPABILITIES-R005` adds the shared request limiter `RateLimiter`.

## Traceability

| Requirement | Design section | Test ID | Evidence |
|---|---|---|---|
| FR-001 | plan.md Capability index | CC-01 to CC-40 |  |
| FR-002 | plan.md Capability index | CC-01 to CC-40 |  |
| SC-001 | plan.md Live integration path | CC-01 to CC-40 |  |
| SC-002 | plan.md Interfaces and data model | CC-40, CC-41 |  |

## User Scenarios & Testing

### User Story 1 - Find the capability before writing code (Priority: P1)

A connector author looks up a capability in the index and reuses the SDK module.

**Acceptance Scenarios**:

1. **Given** a connector that needs paging, **When** the author reads the index, **Then** the row names the SDK iterator and the rule that the connector ships no copy.

### User Story 2 - Review a connector against the index (Priority: P2)

A reviewer checks that a connector ships no copy of a capability.

**Acceptance Scenarios**:

1. **Given** a connector that builds its own HTTP session, **When** `sdk-conformance` runs, **Then** it reports a finding that cites row `SCION-CENTRALIZED-CAPABILITIES-R001`.

### User Story 3 - Deliver a tool's output files to configured sinks (Priority: P1)

An operator configures an object store and a WebDAV file share as sinks. A tool run writes a file. The operator finds the file in both sinks, and the caller gets a link.

**Acceptance Scenarios**:

1. **Given** two configured sinks, **When** a bound upstream tool writes `out.png`, **Then** both sinks hold it under `<namespace>/<date>/<run>/out.png`.
2. **Given** an object-store sink with a signed-URL lifetime, **When** the run ends, **Then** the result links a signed URL.
3. **Given** an input `exchange://files/in/photo.png`, **When** the tool runs, **Then** the tool reads the staged copy.

### Edge Cases

- A row whose conformance method is unchecked reads "unchecked" and is not silently passed.
- An owner row that no `requirements.md` defines fails the index test.
- A module path that moves fails the index test until the row follows.
- A store credential written as a literal refuses at load with `STORAGE_SECRET_LITERAL`.
- A URL input that resolves to a private address refuses before the upstream runs.
- An optional sink that fails leaves the call successful and records `EXCHANGE_PUBLISH_FAILED`.

## Assumptions

- Owner rows keep the contracts; this spec holds only the connector-facing rule.
- The `sdk-conformance` gate checks rows R001, R008, R009, R016 and R040 today.

## Open questions

None.
