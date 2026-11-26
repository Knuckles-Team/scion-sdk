---
name: scion-sdk-development
domain: development
skill_type: skill
description: >-
  Develop, test, and release a change in scion-sdk: the connector
  control and transport layer every MCP connector builds on. Use when adding
  or changing a connector manifest, a typed port implementation, a source
  adapter, an artifact kind, a transport, a sink, or anything under
  scion_sdk/, and when running or releasing this repository's gates.
license: MIT
tags: [scion-sdk, development, connectors, mcp, manifest, gates]
metadata:
  version: '0.1.0'
---

# scion-sdk development

`scion-sdk` is the connector control and transport layer: secure
FastMCP server construction, connector manifests and content publication,
typed extension ports, discovery/certification, and governed HTTP/TLS/
credential handling. It does not own vendor API implementations, durable graph
storage, ontology reasoning, agents, or workflow orchestration — those belong
to the owning connector, `medulla`, and the agent control plane.
`meristem` is not a dependency of this package; keep dependency
direction enforceable by the `phase-direction` gate.

## Step 0: route the feature

Route the feature before any code.

1. Read the [ecosystem boundaries standard](https://github.com/Knuckles-Team/pipelines/blob/main/reference/ecosystem-boundaries.md).
2. Split the feature into parts with its routing procedure.
3. If a part is not owned by scion-sdk, stop work on that part. Write its row in the owner's `specs/`, then link it by ID.
4. Continue here only with the parts that scion-sdk owns.

## Spec-driven flow

Run this before any code. Full table and gates: the Spec-driven development section of `AGENTS.md` and the [spec standard](https://github.com/Knuckles-Team/pipelines/blob/main/reference/spec-standard.md).

1. Read `.specify/memory/constitution.md`; create it from `.specify/templates/constitution-template.md` if absent (`speckit-constitution`).
2. Specify the feature: `speckit-specify` gathers the inputs and writes `specs/<stable-id>/spec.md` and `requirements.md`.
3. Clarify: resolve every `[NEEDS CLARIFICATION: ...]` marker (`speckit-clarify`).
4. Plan: `plan.md` from `.specify/templates/plan-template.md`, including Build contract, Production caller chain and Cross-repo seams (`speckit-plan`).
5. Tasks: `tasks.md` lines `- [ ] T001 [P] [US1] <ROW-ID> ...` (`speckit-tasks`).
6. Analyze: fix the gaps and fill `checklists/requirements.md` (`speckit-analyze`).
7. Implement and land with the delivery contract in this skill (`speckit-implement`); release through pipelines `releases/*.yml` bundles.

Never write code for a row that is not SPECIFIED. Feature directories use the stable spec ID, never a numbered prefix. The state is computed from rows, `Spec:` trailers and test bindings; never write `status.json` or a `Status:` line.

## Delivery contract

Specs are already designed. The work is implementation.

**A row is delivered when**
- its behaviour is fully implemented, as the row says;
- it is wired to its production entry point (a production caller exists outside the new code and its tests);
- every test level its test-spec names passes;
- the tests are bound to the row;
- the gates verify it.

There is no time limit, no size limit per agent or per change, and no rule about how many agents deliver a row.

**Recommendations (optional)**
- Extend first: find the spec row that owns the behaviour (`git grep -n "<term>" -- specs`), cite its ID in the commit `Spec:` trailer, and reuse the existing function or module rather than adding a second implementation.
- Group rows that share an entry point into one bundle and deliver bundles in dependency order, producer first. Split a requirement into `<ID>.<n>` children when it names more than 2 code roots or is cross-repo; the destination repository copies a behaviour first, then the source switches importers and deletes.
- Net-new work can be sliced: `.1` typed model plus validation and refusal tests, `.2` the one entry point that uses it, `.3`+ each further behaviour.
- Every delivered ID gets at least one test bound to it in the same PR. Python: `@pytest.mark.spec("<ID>")`. Rust and TypeScript: a `// spec: <ID>` line directly above the test. Never write generated status files; the state is computed.
- Run the targeted tests for the change; leave the full suite to the main-branch gates.
- Report the PR URL, requirement IDs, test result, and any `ID:<main sha>` proof for rows already on main.

**Before every push**
- Work in a real `git worktree add` from `origin/main`. Never edit a shared checkout, never `git stash`, never `git add -A`, never force-push.
- Run the repo's own pre-commit on the changed files: `uvx --from pre-commit==4.6.0 pre-commit run --files $(git diff --name-only origin/main...HEAD)`. Fix every failure. No `noqa`, `type: ignore`, skip, xfail or whitelist entries to pass a gate.

**Landing**
- Open the pull request and merge it when every required check is green. Mechanical conflicts (generated files, Markdown) resolve by merging `origin/main`; real conflicts are additive in most cases and are resolved, not deferred.
- Full CI on main catches what the pull-request subset missed; regressions are fixed forward.

## Bulk delivery

High-rate delivery follows the [bulk delivery standard](https://github.com/Knuckles-Team/pipelines/blob/main/reference/bulk-delivery.md).

- Four separate stages, run by different workers: writing, batched validation, fixing red results only, and automatic merge on green.
- Writers implement one served-method slice of at most 8 rows, push the branch, and never build, test or wait on CI. They format only and run both hook stages.
- Validation workers build branches in batches and record green as a commit status on the exact head SHA.
- A writer never makes an open design choice. It records the question; the decision is written into the owning spec row first, then the row is implemented.
- Only rows implemented completely, with their bound test, go in the `Spec:` trailer. Remainders are re-queued.

## What the SDK provides

- **Manifest model** (`scion_sdk/manifest/`): the connector manifest
  schema, sync presets, tool-schema fingerprints, and loaders (`model.py`,
  `loader.py`, `presets.py`, `tool_schema.py`, `live_contract.py`).
- **Typed ports** (`scion_sdk/ports/`): one protocol per extension
  boundary — `source_adapter` (extracts records from a source stream),
  `artifact_kind` (lists/validates/maps one kind of MCP-served content,
  including the `skill` kind in `scion_sdk/artifacts/skills.py`),
  `transport` (opens sessions), `sink` (durably accepts batches and content
  packs), `writeback` (applies a Medulla-owned change set through an authorized
  source transport), and `repair_proposals`.
- **Reference adapters** (`scion_sdk/adapters/`): the shipped
  `scion_sdk.source_adapters` entry point (`mcp_tool`) and the event
  feed adapter.
- **Discovery and activation** (`scion_sdk/discovery.py`): a
  connector extension self-registers as a Python entry point in one of four
  groups — `scion_sdk.source_adapters`, `.artifact_kinds`,
  `.transports`, `.sinks` — declared under `[project.entry-points.*]` in its
  own `pyproject.toml`. `discover_extensions(group)` lists what is installed;
  `load_extension(group, name, policy=...)` loads it only when an
  `ActivationPolicy` (e.g. `CertifiedExtensions`) authorizes the exact
  `(group, name, distribution, version)` tuple — there is no permissive
  default. `connector-certify` (`scion_sdk.certify.cli:main`) is the
  separate console script that checks a running connector's declared sync tools
  against their live definitions.
- **Public surface contract** (`pyproject.toml`
  `[tool.scion_sdk.wiring] public_modules`): the declared roots for
  this repository's own wiring gate. A module is reachable when it is in that
  list, is the target of a `[project.entry-points]` value, or is imported
  (directly or transitively) from a reachable module; every public name must
  also be exercised by a test under `tests/`.

## How to build a connector with it

1. Declare each ingestible source with `source_meta(...)` on its read tool
   (`scion_sdk.ingest.live_declaration`); the server serves them at
   `connector://resources`.
2. Implement the typed port(s) the connector needs (`SourceAdapter`,
   `ArtifactKind`, `Transport`, `Sink`, `WriteBackPort`, ...) from
   `scion_sdk/ports/`.
3. Register each implementation as an entry point in the connector's own
   `pyproject.toml`, under the matching `scion_sdk.*` group, so
   `discover_extensions` finds it and an `ActivationPolicy` can certify it.
4. Prove conformance with the reusable suites in
   `scion_sdk/testing/`: `source_adapters` (capability descriptor,
   pagination, checkpoint resume, idempotent re-run, malformed-input
   rejection, provenance completeness), `artifact_kinds` (listing, validation,
   deterministic digests, record mapping, malformed-entry rejection), and
   `writeback` (dry-run, optimistic conflict, authorization, idempotency,
   uncertain-outcome reconciliation) — assert with
   `scion_sdk.testing.results.assert_conformant`.
5. Run `connector-certify -- COMMAND [ARG ...]` against the connector's own MCP
   server to confirm its declared sync tools are valid before it is certified
   for activation.

## How to test it

`AGENTS.md` "Setup" and "Commands" are the source of truth; read them first.
In short:

1. Bootstrap once: `scripts/bootstrap.sh` (idempotent; add `--scanners` for
   the pinned native cccc, KISS, dupehound and jscpd scanners, which need
   cargo and npm). A Claude Code cloud session runs it through
   `.claude/hooks/session-start.sh`.
2. Tests: `uv run --frozen python -m pytest -q`.
3. Gates: `uvx --from pre-commit==4.6.0 pre-commit run --config
   .config/pre-commit.yaml --all-files`, then the same command with
   `--hook-stage pre-push` (tests, wheel build, secret history) and with
   `--hook-stage manual` (native scanners, privacy, fleet gates).

The wiring gates run inside the pre-commit stage as `check-orphan-modules` and
`check-public-api-tested` (`uv run --frozen python scripts/check_wiring.py
orphans|public-api`); the maintainer-only fleet gates `dependency-readiness`
and `phase-direction` (`bash scripts/run_fleet_gate.sh <gate>`) run at the
manual stage and print `SKIPPED (<gate>): <reason>` outside the fleet
workspace layout. While editing a public documentation surface, also run:

```bash
pre-commit try-repo ../pipelines public-surface --all-files
```

A hook whose prerequisite is missing prints `SKIPPED (<gate>): <reason>` and
exits 0 locally; with `CI` set it exits 2 (`CANNOT RUN`). Never bypass, baseline,
or suppress a failing gate — fix its cause.

## How to release it

Hosted CI is `.github/workflows/release.yml`: `gates` (the whole
`.config/pre-commit.yaml` suite, pre-commit then pre-push stage) must pass
before `build` (a byte-identical wheel built twice) runs; the pinned
cccc/KISS/dupehound/jscpd scanners report weekly in `advisory.yml`; `publish-pypi` runs only on a
`v*` tag push, after `uv lock --check`.

This repository is a shared multi-worktree checkout:

- Create a real worktree before changing files —
  `git worktree add <path> -b <branch> main` — never harness-managed worktree
  isolation (`EnterWorktree`), which can alter shared Git configuration for
  every linked checkout.
- Never use `git stash`; its stack is shared by all worktrees.
- Never stage with `git add -A` or `git add .`; stage an explicit path
  allowlist, then inspect `git diff --cached`.
- Do not overwrite another lane's branch, generated files, lockfile, or
  uncommitted changes.
- **Do not use `--no-verify`.** Commit, push, tag, publish, and deploy are
  distinct operations and each needs its own completed gates and authority.
- Work on a topic branch, push it, and open a pull request against `main`
  (draft until gates pass); `gates` and `build` must be green before merge.
