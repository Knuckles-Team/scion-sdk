# SCION-CENTRALIZED-CAPABILITIES data model

The index adds no store. These types are introduced by rows of this spec or named by them.

| Type | File | Fields | Row |
|---|---|---|---|
| `RateLimiter` | scion_sdk/httpsupport/rate_limit.py | minimum interval, clock, sleep | SCION-CENTRALIZED-CAPABILITIES-R005 |
| `PaginationOriginError` | scion_sdk/httpsupport/pagination_walk.py | refusal without URL text | SCION-CENTRALIZED-CAPABILITIES-R004.1 |
| `Storage` (planned) | scion_sdk/a2a/app.py | `list_contexts(caller, cursor, limit)`, event cursor | SCION-CENTRALIZED-CAPABILITIES-R017.1 |
| `ConformanceEvidence` (planned) | scion_sdk/certify/ | tool fingerprints, pack digest, revision | SCION-CENTRALIZED-CAPABILITIES-R041 |
| `ObjectStoreBackend` (planned) | scion_sdk/storage/base.py | bucket and object operations, `capabilities()`, `backend_type` | SCION-CENTRALIZED-CAPABILITIES-R042.1 |
| `ObjectInfo`, `BucketInfo`, `ObjectPage` (planned) | scion_sdk/storage/base.py | Object: key, size, entity tag, change time, media type, metadata. Bucket: name. Page: entries, next token. | SCION-CENTRALIZED-CAPABILITIES-R042.1 |
| `StorageError` (planned) | scion_sdk/storage/base.py | `code` from the storage taxonomy, message without secrets | SCION-CENTRALIZED-CAPABILITIES-R042.1 |
| `StoreConfig` (planned) | scion_sdk/storage/config.py | name, backend, bucket, endpoint, region, root, project, presign endpoint, allowed private hosts, `credentials` references | SCION-CENTRALIZED-CAPABILITIES-R042.6 |
| `ExchangeSettings` (planned) | scion_sdk/exchange/config.py | staging root, input and output limits, output count, retention, URL inputs, lineage, stores, sinks | SCION-CENTRALIZED-CAPABILITIES-R043.1 |
| `SinkDecl` (planned) | scion_sdk/exchange/config.py | name, store, bucket, path template, signed URL lifetime, required | SCION-CENTRALIZED-CAPABILITIES-R043.1 |
| `RunStaging` (planned) | scion_sdk/exchange/staging.py | namespace, run ID, input directory, output directory, start time | SCION-CENTRALIZED-CAPABILITIES-R043.2 |
| `PublishedArtifact`, `ArtifactLocation` (planned) | scion_sdk/exchange/publish.py | name, SHA-256, size, media type, locations; sink, key, signed URL, refusal code | SCION-CENTRALIZED-CAPABILITIES-R043.4 |

## Lineage record (R043.6)

The exchange writes these engine records. Each class and relation exists in the core ontology.

| Record | ID | Properties or ends |
|---|---|---|
| `MediaFile` entity | `artifact:sha256:<hex>` | `contentSha256`, `sizeBytes`, `fileName`, `encodingFormat`, `locations` |
| `Action` entity | `run:<namespace>:<run>` | `tool`, `namespace`, `run`, `caller` |
| `participatedIn` | output to run | none |
| `hasInput` | run to input | none |
| `derivedFrom` | output to input | none |
