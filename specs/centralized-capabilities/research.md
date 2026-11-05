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
