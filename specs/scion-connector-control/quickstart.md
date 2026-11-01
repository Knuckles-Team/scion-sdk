# SCION-CONNECTOR-CONTROL quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-CONNECTOR-CONTROL-R001: `PYTHONNOUSERSITE=1 uv run pytest tests/test_contracts_and_sink.py::test_epistemic_graph_sink_retries_one_pack_head_conflict -q`
- SCION-CONNECTOR-CONTROL-R002: `PYTHONNOUSERSITE=1 uv run pytest tests/test_artifacts_discovery.py::test_contract_pin_normalization_passes_for_a_real_tool -q`
- SCION-CONNECTOR-CONTROL-R003: `PYTHONNOUSERSITE=1 uv run pytest tests/test_certify_missing_output.py::test_served_certification_pins_missing_output_schema -q`
- SCION-CONNECTOR-CONTROL-R004.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_artifacts_discovery.py::test_contract_pin_normalization_rejects_incomplete_tool_sets -q`
- SCION-CONNECTOR-CONTROL-R005: `PYTHONNOUSERSITE=1 uv run pytest tests/test_contracts_and_sink.py::test_sdk_does_not_redeclare_generated_graph_boundary_models -q`
- SCION-CONNECTOR-CONTROL-R006: `PYTHONNOUSERSITE=1 uv run pytest tests/test_decide_consumers.py::test_triage_proposal_never_reaches_write_back -q`
- SCION-CONNECTOR-CONTROL-R007: `PYTHONNOUSERSITE=1 uv run pytest tests/test_major_version_compatibility_served.py::test_fleet_preflight_refusal_preserves_all_pins -q`
- SCION-CONNECTOR-CONTROL-R008: `PYTHONNOUSERSITE=1 uv run pytest tests/test_artifacts_discovery.py::test_tool_cost_and_latency_annotations_reach_generated_pack -q`
- SCION-CONNECTOR-CONTROL-R009: `PYTHONNOUSERSITE=1 uv run pytest tests/test_fleet_migration_scan.py::test_batch_census_is_clean -q`
- SCION-CONNECTOR-CONTROL-R010: `PYTHONNOUSERSITE=1 uv run pytest tests/test_fleet_migration_scan.py::test_batch_census_is_clean -q`
- SCION-CONNECTOR-CONTROL-R011: `PYTHONNOUSERSITE=1 uv run pytest tests/test_fleet_migration_scan.py::test_batch_census_is_clean -q`
- SCION-CONNECTOR-CONTROL-R012: `PYTHONNOUSERSITE=1 uv run pytest tests/test_fleet_migration_scan.py::test_batch_census_is_clean -q`
