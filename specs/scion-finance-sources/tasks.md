# SCION-FINANCE-SOURCES — Implementation tasks

Governing spec: [spec.md](spec.md). Design: [plan.md](plan.md). Rows: [requirements.md](requirements.md).

Each task line reads `- [ ] T001 [P] [US1] <ID>-R012.4 description`. The task number and the row ID are required.
`[P]` marks a task that may run in parallel. `[US1]` names the user story. Tasks are ordered producer first.

- [x] T001 [US1] SCION-FINANCE-SOURCES-R001.1 Finite FRED values.
- [x] T002 [US1] SCION-FINANCE-SOURCES-R001.2 Cross-provider conformance.
- [x] T003 [US1] SCION-FINANCE-SOURCES-R001.3 Refuse same-page calendar identity collisions.
- [x] T004 [US1] SCION-FINANCE-SOURCES-R001.4 FRED page shape.
- [x] T005 [US1] SCION-FINANCE-SOURCES-R001.5 Canonical FRED identity dates.
- [x] T006 [US1] SCION-FINANCE-SOURCES-R001.6 Unique FRED page identities.
- [x] T007 [US1] SCION-FINANCE-SOURCES-R002.1 Complete Alpaca source coverage.
- [x] T008 [US1] SCION-FINANCE-SOURCES-R002.2 Finite market records.
- [x] T009 [US1] SCION-FINANCE-SOURCES-R002.3 Alpaca page shape.
- [x] T010 [US1] SCION-FINANCE-SOURCES-R003.1 Multi-asset source adapters.
- [x] T011 [US1] SCION-FINANCE-SOURCES-R003.2 Finite multi-asset prices.
- [x] T012 [US1] SCION-FINANCE-SOURCES-R003.3 Canonical FX currency identity.
- [ ] T013 [US1] SCION-FINANCE-SOURCES-R003.4 Provider-specific multi-asset source coverage.
- [ ] T014 [US1] SCION-FINANCE-SOURCES-R004.1 Account source and durable re-import.
- [x] T015 [US1] SCION-FINANCE-SOURCES-R004.2 Finite account values.
- [x] T016 [US1] SCION-FINANCE-SOURCES-R004.3 Reject duplicate CSV headers.
- [x] T017 [US1] SCION-FINANCE-SOURCES-R004.4 Reject ragged CSV rows.
- [x] T018 [US1] SCION-FINANCE-SOURCES-R004.5 ASCII currency syntax.
- [x] T019 [US1] SCION-FINANCE-SOURCES-R004.6 Redact invalid currency cells.
- [x] T020 [US1] SCION-FINANCE-SOURCES-R005.1 Complete macro panel sources.
- [x] T021 [US1] SCION-FINANCE-SOURCES-R005.3 ETF page shape.
- [x] T022 [US1] SCION-FINANCE-SOURCES-R005.2 Finite ETF values.
- [x] T023 [US1] SCION-FINANCE-SOURCES-R900.1 Model.
- [x] T024 [US1] SCION-FINANCE-SOURCES-R900.2 Behavior.
- [x] T025 [US1] SCION-FINANCE-SOURCES-R900.3 Wiring.
- [x] T026 [US1] SCION-FINANCE-SOURCES-R900.4 Refusal.
- [x] T027 [US1] SCION-FINANCE-SOURCES-R900.5 Evidence.
- [x] T028 [US1] SCION-FINANCE-SOURCES-R900.6 Hot-path proof.
- [x] T029 [US1] SCION-FINANCE-SOURCES-R900.8 Bind confirmed payment records to proofs.
- [x] T030 [US1] SCION-FINANCE-SOURCES-R900.7 Independent market bar intervals.
- [x] T031 [US1] SCION-FINANCE-SOURCES-R900.9 Positive bar intervals.
- [x] T032 [US1] SCION-FINANCE-SOURCES-R900.10 Finite live ticks.
- [x] T033 [US1] SCION-FINANCE-SOURCES-R900.11 Funding source time.
- [x] T034 [US1] SCION-FINANCE-SOURCES-R001.7 Bounded exchange history windows.
- [x] T035 [US1] SCION-FINANCE-SOURCES-R001.8 Exchange client in the market extra.
- [x] T036 [US1] SCION-FINANCE-SOURCES-R001.9 Calendar page identity and time.
- [ ] T037 [US1] SCION-FINANCE-SOURCES-R901 Probabilistic series forecast provider contract.
- [ ] T038 [US1] SCION-FINANCE-SOURCES-R902 Compute adapter binds quant analyses to NumericEvaluate.
- [ ] T039 [US1] SCION-FINANCE-SOURCES-R903 Strategy research record sink.
- [ ] T040 [US1] SCION-FINANCE-SOURCES-R904 Typed finance compute facade.
