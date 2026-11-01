# SCION-QUALITY-RELEASE — Implementation tasks

Governing spec: [spec.md](spec.md). Design: [plan.md](plan.md). Rows: [requirements.md](requirements.md).

Each task line reads `- [ ] T001 [P] [US1] <ID>-R012.4 description`. The task number and the row ID are required.
`[P]` marks a task that may run in parallel. `[US1]` names the user story. Tasks are ordered producer first.

- [x] T001 [US1] SCION-QUALITY-RELEASE-R001 SDK release builds against a published compatible graph-client wheel.
- [x] T002 [US1] SCION-QUALITY-RELEASE-R002 Public-documentation scanner catches private addresses and hosts.
- [x] T003 [US1] SCION-QUALITY-RELEASE-R003 Release workflow builds with the pinned managed Python, not the system one.
- [x] T004 [US1] SCION-QUALITY-RELEASE-R004 Public documentation follows a task-first README and site structure.
- [x] T005 [US1] SCION-QUALITY-RELEASE-R005.1 The SDK ships connector conformance as a test plugin.
- [x] T006 [US1] SCION-QUALITY-RELEASE-R006 Metric scanners report weekly and never gate CI.
