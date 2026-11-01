# SCION-SCM — SDK source client and virtual workspace

**Requirement IDs:** SCION-SCM-R001, SCION-SCM-R002, and child rows SCION-SCM-R001.1 through SCION-SCM-R001.6 and SCION-SCM-R002.1 through SCION-SCM-R002.6.
**Owner:** scion-sdk.

## Outcome and boundary

An agent or host composes one typed SDK client for the served `source.*`
operations. Those operations are tree, blob, diff, history, blame, commit,
land, and revert. Reads return bounded pages, and blob content is served
from a local bounded cache keyed by content digest. A virtual workspace mount materializes files on read
and records writes as a pending changeset. The mount is sparse by declared
path prefix and performs no repository clone. The served operations, their
admission, and path-scope authorization belong to the serving side. The SDK
owns the client, the cache, and the mount. The SDK serves nothing itself.

## Architecture

```text
served source.* operation (connector or host)
        │
        ▼
SDK source client (typed method per operation, stable continuations)
        │  local bounded blob cache, keyed by content digest
        ▼
virtual workspace mount (sparse by declared prefix)
        │  reads materialize blobs; writes append pending changeset entries
        ▼
flush submits the pending changeset as one commit through the client
```

1. The source client exposes one frozen typed method per `source.*` operation.
   Each request binds repository identity, revision or branch reference, a
   path prefix where the operation takes one, and typed parameters. Each
   result is a typed page or receipt with a stable continuation. No method
   accepts or returns an untyped payload.
2. A blob read consults the local cache first. Cached bytes are served only
   while the stored digest matches the served blob digest. The cache is
   bounded by entry count and byte count, and evicts
   least-recently-used entries. Cache storage is local to the process or
   host. It is never an authority: a miss or mismatch re-reads through the
   served operation.
3. The virtual workspace mount is sparse by declared path prefix. Directory
   entries resolve lazily from the served tree operation. A file read
   materializes content through the client's blob path. A write appends one
   entry to the pending changeset, with path, operation, and new blob
   reference. The mounted revision is never mutated. A flush submits the
   pending changeset as one commit through the client.
4. The SDK ships no server, no tool registration for source operations, and
   no admission logic. The repository-manager connector composes the client
   behind its served development tools inside arbora MCP. A host may compose
   the same client directly.
5. The client and the mount reuse the governed HTTP/TLS stack, credential
   references, and typed-error conventions in `http/`, `tls/`, and
   `credentials/`. They are new modules. The SDK introduces no second
   transport, cache, or clone authority.

## Why a new spec

Search: `git grep -n "source\." -- specs` plus a read of every family in
`specs/`. `SCION-REPOSITORY-TRANSPORT` owns one-way authenticated snapshot
paging into the graph service's `IndexRepository` operation; it has no
interactive client and no write path. `SCION-SOURCE-INGEST` owns source sync,
drift quarantine, and adapter migration; it ingests external sources and does
not serve development reads or edits. `SCION-CONNECTOR-CONTROL` owns hosting,
discovery, and certification; it owns no source client or workspace. No
existing spec owns the typed development source client or the virtual
workspace mount, so this spec is new. The serving side and the repository-manager
connector own the served `source.*` operations. This spec links them by
operation name and by requirement ID, pending filing in the owning
repositories.

## Scope and interfaces

Owned: the typed source client and its local bounded blob cache
(SCION-SCM-R001); the sparse virtual workspace mount and its pending changeset
(SCION-SCM-R002). Not owned: the served `source.*` operations and their
admission. Epistemic-graph owns changeset storage, lock-free acceptance,
and derived history indexes. Agent-utilities owns the in-run development
loop. Pipelines owns gate policy around landing. The client consumes the served
operations as they are published. A reimplementation of a served operation in
the SDK is a defect.

Requirement IDs are defined in [requirements.md](requirements.md); delivery
state per ID is in `status.json`.

## Evidence and completion

| Gate | State | Evidence |
|---|---|---|
| Source client composed through a served operation | OPEN | Pending commit and served test |
| Virtual workspace mount read/write/flush proof | OPEN | Pending commit and served test |

Definition only: no implementation and no runtime receipt exist for this
spec yet.

## User Scenarios & Testing

### User Story 1 - SDK source client and virtual workspace (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: An agent or host composes one typed SDK client for the served `source.*` operations.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-SCM-R001, **When** it exercises "Source client", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- Refusal: the row SCION-SCM-R001.4 states the refusal or bound and its test proves it.
- Refusal: the row SCION-SCM-R002.4 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: Source client. Rows: SCION-SCM-R001.
- **FR-002**: Virtual workspace mount (rollup). Rows: SCION-SCM-R002.

## Success Criteria

- **SC-01**: Source client against a controlled served source.* backend. Rows: SCION-SCM-R001.2.
- **SC-02**: Blob read, warm and cold, same served digest. Rows: SCION-SCM-R001.6.
- **SC-03**: Cache exceeds its entry-count and byte bounds. Rows: SCION-SCM-R001.
- **SC-04**: Served digest differs from the cached digest for one entry. Rows: SCION-SCM-R001.
- **SC-05**: Identical commit retry after simulated transport loss. Rows: SCION-SCM-R001.2.
- **SC-06**: unauthorized, not_found, conflict, path_denied, oversized, deadline_exceeded, transport_unavailable refusals. Rows: SCION-SCM-R001.4.
- **SC-07**: Mount over the client for declared prefixes; read inside and outside them. Rows: SCION-SCM-R002.2.
- **SC-08**: Write three paths, then flush; a second flush after a conflicting base change. Rows: SCION-SCM-R002.2.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
