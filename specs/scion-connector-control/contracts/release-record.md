# Release record seam: the digests the runner reads

Provider: pipelines PIPE-RELEASE-R009
Consumer: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.4

## Wire shape

The release workflow records every connector release through the arbora operation `release.record`, which calls the engine's `RecordRelease`. The request carries `repo` (the distribution name), `version`, `git_ref`, `changeset_head`, `published_at` and `artifact_digests`. `artifact_digests` is the list printed by `connector-release-digest`: `sha256:<hex>` for the wheel and the sdist and `content-sha256:<hex>` for the wheel content digest. The engine seals the record as the node `release:<repo>:<version>` and refuses an overwrite. The runner reads `artifact_digests` from that node.

## Refusal codes

- Engine: `RELEASE_HEAD_UNKNOWN`, `RELEASE_VERSION_TAKEN`, `RELEASE_SEALED`.
- Runner: `RELEASE_PIN_UNAVAILABLE` when the node cannot be read and `RELEASE_PIN_MISSING` when it holds no digest.

## Authoritative types

- `scion_sdk/runner/release_pins.py` `MedullaReleasePins` and `release_node_id`.
- `scripts/release_record.py` in pipelines builds the request.

## Test vectors

- `tests/test_runner_release.py::test_pins_come_from_the_caller_or_the_engine_record` reads a sealed node.
- pipelines `tests/test_release_record_step.py::test_step_posts_release_with_digests` posts the request.

## Owner rows

- Provider: pipelines PIPE-RELEASE-R009.
- Consumer: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.4.
