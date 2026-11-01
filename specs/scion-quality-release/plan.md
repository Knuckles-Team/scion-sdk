# SCION-QUALITY-RELEASE — Implementation plan

Governing spec: [spec.md](spec.md). Rows: [requirements.md](requirements.md).

## Summary

Delivery phase: P1

This plan covers the 7 active rows of SCION-QUALITY-RELEASE. Each row names its production caller chain, the files it edits and the existing helpers it reuses; the design below groups them. The SDK owns the connector side; the engine and the door own their sides by row ID.

## Technical Context

- **Language/Version**: Python 3.14 (floor 3.12)
- **Primary Dependencies**: pydantic, httpx, the generated Medulla client types and the MCP libraries pinned in pyproject.toml
- **Storage**: N/A (durable state lives in the engine; the SDK holds checkpoints and bounded caches)
- **Testing**: pytest with spec markers; each row names its test as path::name
- **Target Platform**: Linux containers and developer workstations
- **Project Type**: library with console scripts
- **Performance Goals**: bounded pages, retries and caches as each row states; no unbounded growth
- **Constraints**: no private data in the repository; engine contract read from the published package only
- **Scale/Scope**: 7 active rows

## Constitution Check

- R-I The SDK owns connector contracts and transports: pass. Each capability is implemented here once and connectors reuse it.
- R-II The public API is tested and reachable: pass. Every row names a test and a production caller.
- R-III The engine graph contract is external: pass. The contract is read from the published package.

## Project Structure

```text
scion_sdk/   existing package; each row names its files
tests/       existing tests; each row names path::name
specs/scion-quality-release/   existing spec files
```

## Phase 0: Research

no unknowns

## Phase 1: Design

1. Inventory current hooks and classify each by source-only inputs versus environment-backed acceptance inputs. Preserve correctness and security checks with reproducible dependencies.
2. Remove stale copied-doc synchronization from PR critical path; consolidate maintained public guide source into the Pages build. Validate links and privacy on that source.
3. Provision scanner/tool versions and synthetic fixtures inside CI so cloud PRs reproduce the local source gate.
4. Build the SDK wheel and test an isolated installed consumer against the locked published graph wheel. Release and Pages jobs independently record artifact and URL evidence.

### R005 published-wheel provenance

Pin `medulla` to its PyPI wheels in `uv.lock`, by SHA256. Forbid a
source build with `[tool.uv] no-build-package`. Bootstrap installs the locked
environment with `uv sync --frozen`. Graph inspection requires SDK-consumed
Python modules from the installed wheel package. The release record names one
locked wheel. Record graph and provenance fixtures only from this prepared
environment. Never synthesize installation evidence.

### Amendments

2026-10-27: Run CI and the manual fleet gates on the project interpreter in
`.python-version`. Remove the separate Python 3.12 PR job. Package metadata
continues to declare the existing Python support floor.

### Amendment: job-scoped uv cache placement (R005)

The shared gates caller selects an ignored workspace-local uv cache directory
for each run and attempt. This keeps the cache alongside the project environment
when the runner uses one filesystem. The shared workflow restores the same
lock-keyed cache; dependency and security checks remain unchanged. Measure cache and environment device IDs and hardlink support on the runner.
Compare setup times for serial and concurrent jobs before claiming a speedup.
Allow 45 minutes for the PR gate. This gives quality checks time to finish
during storage stalls.


### 2026-10-28: local Medulla dependency migration candidate

R001 and R005 retain their released-wheel qualification gates. The local
candidate requires `medulla>=2.27.10,<3.0.0` instead of the retired distribution.
Imports remain `medulla`. Candidate-wheel experiments must not install
both distributions into that shared namespace. The exact hosted Medulla wheel
is prerelease evidence here, not a published-release claim. Fresh lock, release
record and isolated installed acceptance remain required before release or
promotion. Reviewers may publish the draft source only to build this candidate.
They must qualify it with the official Medulla wheel from the same run.
This does not satisfy release gates or update the unchanged release lock.


## Phase 2: Task approach

tasks.md holds one task line per active row, ordered producer first; rows that share no file are marked parallel by their order.

## Complexity Tracking

none

## Build contract

Gates are the repository pre-commit and pre-push hooks (`uvx pre-commit run -c .config/pre-commit.yaml --hook-stage manual --all-files`): KISS limits from `.config/kiss.toml`, duplicate scans, type check, orphan-module and public-API-tested checks, and the spec hook. No scanner gaming and no duplicate implementation.

## Production caller chain

Console scripts and connector imports reach the owners below; each row's Caller chain names its own path.

- `scion_sdk/connector.py` (line 1): cited by 2 rows; its importers are named in each row's Caller chain.
- `scion_sdk/__init__.py` (line 18): cited by 1 rows; its importers are named in each row's Caller chain.
- `scion_sdk/cli.py` (line 18): cited by 1 rows; its importers are named in each row's Caller chain.
- `scion_sdk/testing/conformance.py` (line 47): cited by 1 rows; its importers are named in each row's Caller chain.
- `scion_sdk/testing/ontology_rollup.py` (line 26): cited by 1 rows; its importers are named in each row's Caller chain.

## Cross-repo seams

none. Dependencies on other repositories are `Depends on:` rows that cite the owner row ID; this spec adds no new wire contract.
