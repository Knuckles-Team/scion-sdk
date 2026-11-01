# SCION-CENTRALIZED-CAPABILITIES data model

The index adds no store. These types are introduced by rows of this spec or named by them.

| Type | File | Fields | Row |
|---|---|---|---|
| `RateLimiter` | scion_sdk/httpsupport/rate_limit.py | minimum interval, clock, sleep | SCION-CENTRALIZED-CAPABILITIES-R005 |
| `PaginationOriginError` | scion_sdk/httpsupport/pagination_walk.py | refusal without URL text | SCION-CENTRALIZED-CAPABILITIES-R004.1 |
| `Storage` (planned) | scion_sdk/a2a/app.py | `list_contexts(caller, cursor, limit)`, event cursor | SCION-CENTRALIZED-CAPABILITIES-R017.1 |
| `ConformanceEvidence` (planned) | scion_sdk/certify/ | tool fingerprints, pack digest, revision | SCION-CENTRALIZED-CAPABILITIES-R041 |
