# SCION-CENTRALIZED-CAPABILITIES research

Governing plan: [plan.md](plan.md). This file records the decisions behind rows R042 and R043.

## 1. Where the storage backends live

Decision: the SDK owns one backend protocol and every backend, in `scion_sdk/storage/`.

Rationale: two connectors serve overlapping stores. The object-store connector carried four backends, and the file-share connector carried its own WebDAV calls. A third consumer, the artifact exchange, needs the same backends. One copy keeps the error taxonomy, key validation and egress rules in one place.

Rejected: the exchange imports the object-store connector. A connector must depend on the SDK only, so that dependency points the wrong way.

Rejected: a thin adapter over each connector's client. Each adapter duplicates the protocol in its connector.

The move is a hard cutover. The object-store connector deletes its copy in the same release that pins the SDK version with the backends.

## 2. A per-call working directory for a pooled child

Decision: the exchange passes one input tree and one output tree as the child's roots, once, at spawn. Each call works in its own run directory under those trees. The proxy rewrites relative path arguments into the run's output directory.

Rationale: a pooled child keeps open-document state across calls and receives its roots only at spawn. A new root per call needs a new process per call.

Rejected: one process per run. Start-up cost and lost document state rule it out.

Rejected: a scan of the whole write root after each call. Two concurrent calls then claim each other's outputs.

The read and write trees stay disjoint, because the registry refuses overlapping roots.

## 3. Input forms and egress

Decision: an input is either `exchange://<sink>/<key>` or an `https` URL. Plain `http` is allowed for loopback only. A URL download uses the governed client with address pinning, so a private address refuses.

Rationale: an explicit scheme makes the input unambiguous. A bare path cannot be told apart from a local path that the child reads under its own roots.

Rejected: a sink prefix such as `files:<path>`. It collides with drive letters and with URL schemes.

## 4. Lineage without a new engine type

Decision: the exchange writes one change set through the existing ingest facade. It uses the core classes `MediaFile` and `Action` and the relations `participatedIn`, `hasInput` and `derivedFrom`.

Rationale: the core ontology already holds a content-identity key, `contentSha256`, on `MediaFile`. It also holds an action class and the input and derivation relations. A new class duplicates them.

Rejected: an engine blob upload of every output. The bytes already live in the sinks, and a second copy doubles the storage.

## Comparison with a Linked Data operation algebra and a knowledge platform (rows R052 to R055)

Web-Algebra implements `GET`, `PUT`, `POST` and `PATCH` of RDF resources over HTTP. It also implements the SPARQL forms `SELECT`, `CONSTRUCT` and `DESCRIBE`, and schema extraction from an endpoint. Its reconciliation call targets a service that follows the W3C Entity Reconciliation Service API. It signs requests to its companion hub with a client certificate. TrustGraph has an embedding client, document decoders and tool services that register external tools.

| Capability | Spec row | State |
|---|---|---|
| Read and write RDF resources over HTTP with media-type negotiation | `SCION-CENTRALIZED-CAPABILITIES-R055` | New |
| Reach host services and logs on every supported operating system through one interface | `SCION-CENTRALIZED-CAPABILITIES-R056` | New |
| Query and update a remote SPARQL endpoint | `SCION-CENTRALIZED-CAPABILITIES-R052` | New |
| Induce an ontology profile from the instance data of an endpoint | `SCION-CENTRALIZED-CAPABILITIES-R053` | New |
| Entity reconciliation against a service that follows the W3C API | `SCION-CENTRALIZED-CAPABILITIES-R054` | New |
| Client certificate for outbound calls | `SCION-CENTRALIZED-CAPABILITIES-R008` (TLS profiles) | Covered |
| Governed HTTP, egress guard, retry, redaction | `SCION-CENTRALIZED-CAPABILITIES-R001`, `R002`, `R006` | Covered |
| GraphQL, MCP and A2A client transports | `SCION-CENTRALIZED-CAPABILITIES-R014`, `R016`, `R017` | Covered |
| Embedding client | `SCION-CENTRALIZED-CAPABILITIES-R025` | Covered |
| Document decoders and OCR in the ingest runner | `SCION-SOURCE-INGEST-R908` | Covered |
| External tool services registered with the platform | `SCION-CENTRALIZED-CAPABILITIES-R015`, `R028`, `R029` (tool surface, declaration, certification) | Covered |
| Ontology pack generation from typed declarations | `SCION-CENTRALIZED-CAPABILITIES-R027` | Covered |
| Hub-specific operations for one product (container, view, chart blocks) | none | Not adopted in the SDK. A connector repository for that product must own them (`CONN-<REPO>-`), and none exists. |

Decisions. The clients raise categories without server text, because the project's errors surface raw server responses. The default result cap of the SPARQL client is the value that Arbora already uses for its endpoint (`ARBORA-OPS-R857.1`). The profile thresholds are the starting defaults of `MEDULLA-INGEST-WORKER-R002.8`.
