# SCION-SOURCE-INGEST data model

This spec adds no new persisted type. The shapes below are the declared configuration and records its rows use, all in existing modules.

- `ConnectorDescriptor` (`scion_sdk/runner/descriptors.py`): the one configuration root of the runner. Its `repository` block holds `batch_files` (default 200, range 1 to 5000) and `peak_bytes` (default 1073741824, range 67108864 to 17179869184) for the repository delta index (SCION-SOURCE-INGEST-R004.14).
- Index mode: `typed` runs type analysis and `syntax` skips it; the languages come from the declared matrix.
- `SourceCheckpoint`: the only progress record of the runner; the engine returns the changed file set from the accepted checkpoint.
