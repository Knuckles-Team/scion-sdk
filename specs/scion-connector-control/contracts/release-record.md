# Release record seam: the digests the runner reads

Provider: arbora ARBORA-HOST-R037
Consumer: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.4

## Wire shape

Arbora records a connector release when it onboards the connector version, through the engine's `RecordRelease` method (the method the operation `release.record` serves). It reads the wheel and sdist sha256 digests from the package index's JSON metadata for that exact distribution and version, downloads the wheel, checks its sha256 against the index, and computes the wheel content digest with `wheel_release_evidence`. The request carries `repo` (the distribution name), `version`, `git_ref`, `changeset_head`, `published_at` (the wheel's index upload time in seconds) and `artifact_digests`: `sha256:<hex>` for the wheel and the sdist and `content-sha256:<hex>` for the wheel content digest, the same strings `connector-release-digest` prints. The engine seals the record as the node `release:<repo>:<version>` and stores the whole record as the JSON text property `release_record`. A replay of identical bytes succeeds and a different record for the version is refused. The runner reads `artifact_digests` from `release_record`.

## Refusal codes

- Engine: `RELEASE_HEAD_UNKNOWN`, `RELEASE_VERSION_TAKEN`, `RELEASE_SEALED`.
- Runner: `RELEASE_PIN_UNAVAILABLE` when the node cannot be read and `RELEASE_PIN_MISSING` when it holds no digest.

## Authoritative types

- `scion_sdk/runner/release_pins.py` `MedullaReleasePins` and `release_node_id`.
- arbora `arbora/fleet/release_record.py` builds the request.

## Test vectors

- `tests/test_runner_release.py::test_pins_come_from_the_caller_or_the_engine_record` reads a sealed node in the engine's stored shape.
- arbora `tests/fleet/test_release_record.py::test_onboarding_records_the_release_from_the_index_and_the_runner_accepts` records one.

## Owner rows

- Provider: arbora ARBORA-HOST-R037.
- Consumer: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.4.
