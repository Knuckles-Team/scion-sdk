# Evidence: the release consumer gate run for real (2026-11-12)

Row: `SCION-QUALITY-RELEASE-R007` (a candidate SDK wheel imports and collects in pinned consumers before it is uploaded).

Until this run the gate had only been exercised with a stub command runner. This run built the wheel of scion-sdk main (0.10.1,
commit 16603b21) on a Python worker and ran `scripts/check_release_consumers.py` against the two consumers in
`scripts/release_consumers.toml`: each is cloned, installed into a fresh environment with the wheel overriding its scion-sdk
requirement, imported, and its tests collected.

## What the real run found

1. **The override dropped the consumer's extras.** `--override` replaces a requirement whole, so `scion-sdk[graphql,mcp,requests]`
   became a bare `scion-sdk` and the packages the extras bring were missing. Fixed in `scripts/check_release_consumers.py`:
   the override now carries the extras the consumer itself declares in its `pyproject.toml` (bound test
   `tests/test_release_consumer_gate.py::test_the_override_keeps_the_extras_the_consumer_declares`).
2. **gitlab-api did not declare what it imports.** After the override fix, `import gitlab_api` still failed: the API client imports
   `requests` and the models use email-typed fields, but neither the SDK `requests` extra nor `pydantic[email]` was declared.
   Fixed in gitlab-api 27.3.23 (declares both); the SDK change merges after it.

## Result after both fixes

| Consumer | Ref | Install | Import | Test collection |
|---|---|---|---|---|
| arbora | main | ok | ok | ok |
| gitlab-api | 27.3.23 branch (main once merged) | ok | ok | ok (385 of 395 collected, 10 deselected) |

The script exited 0. The first run (before either fix) exited 1 with `consumer gitlab-api failed: ... -c import gitlab_api`.
