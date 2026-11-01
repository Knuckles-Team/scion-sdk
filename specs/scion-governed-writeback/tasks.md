# SCION-GOVERNED-WRITEBACK — Implementation tasks

Governing spec: [spec.md](spec.md). Design: [plan.md](plan.md). Rows: [requirements.md](requirements.md).

Each task line reads `- [ ] T001 [P] [US1] <ID>-R012.4 description`. The task number and the row ID are required.
`[P]` marks a task that may run in parallel. `[US1]` names the user story. Tasks are ordered producer first.

- [ ] T001 [US1] SCION-GOVERNED-WRITEBACK-R001.1 Model.
- [x] T002 [US1] SCION-GOVERNED-WRITEBACK-R001.2 Behavior.
- [ ] T003 [US1] SCION-GOVERNED-WRITEBACK-R001.3 Wiring.
- [x] T004 [US1] SCION-GOVERNED-WRITEBACK-R001.4 Refusal.
- [x] T005 [US1] SCION-GOVERNED-WRITEBACK-R001.5 Evidence.
- [ ] T006 [US1] SCION-GOVERNED-WRITEBACK-R001.6 Hot-path proof.
- [x] T007 [US1] SCION-GOVERNED-WRITEBACK-R002 Source-side WriteBackPort composes a durable graph write-back adapter.
- [x] T008 [US1] SCION-GOVERNED-WRITEBACK-R003 Full write-back requires an authorization resolver and restart recovery.
- [ ] T009 [US1] SCION-GOVERNED-WRITEBACK-R004.1 Generic approval residual.
- [ ] T010 [US1] SCION-GOVERNED-WRITEBACK-R004.2 Generic deterministic replay residual.
- [ ] T011 [US1] SCION-GOVERNED-WRITEBACK-R004.3 Generic audit reservation residual.
- [ ] T012 [US1] SCION-GOVERNED-WRITEBACK-R004.4 Business-rule API residual.
- [ ] T013 [US1] SCION-GOVERNED-WRITEBACK-R004.5 Typed repository effect request.
- [ ] T014 [US1] SCION-GOVERNED-WRITEBACK-R004.6 Repository path and scope resolution.
- [ ] T015 [US1] SCION-GOVERNED-WRITEBACK-R004.7 Approval binds the complete source effect.
- [ ] T016 [US1] SCION-GOVERNED-WRITEBACK-R004.8 Conditional provider mutation.
- [ ] T017 [US1] SCION-GOVERNED-WRITEBACK-R004.9 Actual immutable effect receipt.
- [ ] T018 [US1] SCION-GOVERNED-WRITEBACK-R004.10 Independent revision readback.
- [ ] T019 [US1] SCION-GOVERNED-WRITEBACK-R004.11 Repository restart reconciliation.
- [ ] T020 [US1] SCION-GOVERNED-WRITEBACK-R004.12 Served repository producer composition.
- [x] T021 [US1] SCION-GOVERNED-WRITEBACK-R004.13 Reject invalid audit reservation identifiers.
- [x] T022 [US1] SCION-GOVERNED-WRITEBACK-R004.14 Uncertain reconciliation cannot authorize retry.
- [x] T023 [US1] SCION-GOVERNED-WRITEBACK-R004.15 Exact dry-run display.
- [x] T024 [US1] SCION-GOVERNED-WRITEBACK-R004.16 Recheck the approved change before apply.
- [x] T025 [US1] SCION-GOVERNED-WRITEBACK-R004.17 Refuse overlapping effects in one connector.
- [x] T026 [US1] SCION-GOVERNED-WRITEBACK-R005 Write-back implementation passes its full durable test suite together.
- [ ] T027 [US1] SCION-GOVERNED-WRITEBACK-R006 Execute a released governed change set.
