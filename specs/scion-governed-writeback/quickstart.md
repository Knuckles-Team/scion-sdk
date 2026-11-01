# SCION-GOVERNED-WRITEBACK quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-GOVERNED-WRITEBACK-R001: `PYTHONNOUSERSITE=1 uv run pytest tests/test_decide_consumers.py::test_writeback_is_only_ever_a_proposal -q`
- SCION-GOVERNED-WRITEBACK-R001.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_wallet_proof_contract.py::test_intent_and_envelope_models_are_closed -q`
- SCION-GOVERNED-WRITEBACK-R001.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_wallet_proof_apply.py::test_gate_binds_envelope_and_replay_returns_prior_effect_without_fresh_proof -q`
- SCION-GOVERNED-WRITEBACK-R001.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_wallet_proof_apply.py::test_run_writeback_passes_the_gate_for_a_wallet_sink -q`
- SCION-GOVERNED-WRITEBACK-R001.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_wallet_proof_apply.py::test_every_proof_refusal_makes_no_effect -q`
- SCION-GOVERNED-WRITEBACK-R001.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_wallet_proof_apply.py::test_evidence_is_joined_redacted_and_survives_restart -q`
- SCION-GOVERNED-WRITEBACK-R001.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_wallet_proof_served.py::test_served_wallet_proof_and_governed_apply -q`
- SCION-GOVERNED-WRITEBACK-R002: `PYTHONNOUSERSITE=1 uv run pytest tests/test_writeback_durable.py::test_generated_eg_adapter_uses_typed_ops_and_idempotency_keys -q`
- SCION-GOVERNED-WRITEBACK-R003: `PYTHONNOUSERSITE=1 uv run pytest tests/test_writeback_durable.py::test_durable_authorization_resolver_fails_closed -q`
- SCION-GOVERNED-WRITEBACK-R004: `PYTHONNOUSERSITE=1 uv run pytest tests/test_writeback_audit_reservation.py::test_invalid_reservation_never_applies -q`
- SCION-GOVERNED-WRITEBACK-R004.9: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_source_effect_served.py::test_effect_receipt -q`
- SCION-GOVERNED-WRITEBACK-R004.12: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_source_effect_served.py::test_served_journey -q`
