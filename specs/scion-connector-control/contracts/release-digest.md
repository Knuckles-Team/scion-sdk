# Release digest seam with the release workflow and the arbora host

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.2
Provider: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.4
Consumer: pipelines PIPE-RELEASE-R019
Consumer: arbora ARBORA-HOST-R037

## Wire shape

`connector-release-digest WHEEL [--sdist FILE]` prints one JSON object: `distribution`, `version`, `wheel_sha256`, `sdist_sha256` (null without `--sdist`), `content_sha256` and `artifact_digests`. `artifact_digests` is a sorted list of strings: `sha256:<hex>` for the wheel and for the sdist, and `content-sha256:<hex>` for the wheel content digest. The release workflow prints that list in its job summary and posts nothing. Arbora derives the same list from the package index and the indexed wheel and records it as the `artifact_digests` of the engine's `RecordRelease` request, with `repo` set to the distribution name and `version` set to the version. The engine stores a sealed `Release` node `release:<repo>:<version>` holding the record as `release_record`. The runner reads that record's `artifact_digests` and requires `content-sha256:<reported>`, where `<reported>` is the `content_sha256` the connector serves at `connector://release`. There is no key, signature or operator file.

## Refusal codes

- Command: exit 2 with a reason on stderr and nothing on stdout for an unreadable wheel.
- Runner: `RELEASE_DIGEST_UNREPORTED`, `RELEASE_EVIDENCE_INVALID`, `RELEASE_PIN_UNAVAILABLE`, `RELEASE_PIN_MISSING`, `RELEASE_DIGEST_MISMATCH`.
- Host: `RunnerConfigurationError` when the runner has neither an engine client nor injected pins.

## Authoritative types

- `scion_sdk/manifest/release_digest.py` `ReleaseEvidence`, `wheel_release_evidence`, `installed_release_evidence`.
- `scion_sdk/ports/release_pins.py` `ReleasePins` and `scion_sdk/runner/release_pins.py` `MedullaReleasePins`.

## Test vectors

- `tests/test_release_cli.py::test_digest_command_prints_pins_and_refuses_a_bad_wheel` holds the command output shape.
- `tests/test_runner_release.py::test_each_code_blocks_provisioning` holds one vector per refusal code.

## Owner rows

- Provider: scion-sdk SCION-CONNECTOR-CONTROL-R650.1.2 and SCION-CONNECTOR-CONTROL-R650.1.4.
- Consumer: pipelines PIPE-RELEASE-R019.
- Consumer: arbora ARBORA-HOST-R037.
