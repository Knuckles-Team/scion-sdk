# SCION-REPOSITORY-TRANSPORT — Implementation tasks

Governing spec: [spec.md](spec.md). Design: [plan.md](plan.md). Rows: [requirements.md](requirements.md).

Each task line reads `- [ ] T001 [P] [US1] <ID>-R012.4 description`. The task number and the row ID are required.
`[P]` marks a task that may run in parallel. `[US1]` names the user story. Tasks are ordered producer first.

- [x] T001 [US1] SCION-REPOSITORY-TRANSPORT-R001 Repository transport hydrates code and specification sources from Git.
- [ ] T002 [US1] SCION-REPOSITORY-TRANSPORT-R002 SDK is the sole path for repository hydration.
- [x] T003 [US1] SCION-REPOSITORY-TRANSPORT-R003 Repository ref walk traverses each Git tree once with batched reads.
- [x] T004 [US1] SCION-REPOSITORY-TRANSPORT-R004 Repository transport submits bounded, outcome-complete index batches.
- [x] T005 [US1] SCION-REPOSITORY-TRANSPORT-R005 Branch-aware transport batches indexing into one call per batch.
- [x] T006 [US1] SCION-REPOSITORY-TRANSPORT-R006 Provider pages respect their requested bound.
- [x] T007 [US1] SCION-REPOSITORY-TRANSPORT-R007 Local Git blob reads stay within the selected revision.
