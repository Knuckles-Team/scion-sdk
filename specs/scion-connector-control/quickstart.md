# SCION-CONNECTOR-CONTROL quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-CONNECTOR-CONTROL-R001: `PYTHONNOUSERSITE=1 uv run pytest tests/test_contracts_and_sink.py::test_medulla_sink_retries_one_pack_head_conflict -q`
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
- SCION-CONNECTOR-CONTROL-R914: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_registry_served.py::test_two_stdio_upstreams_are_namespaced_and_lazy -q`
- SCION-CONNECTOR-CONTROL-R914.1: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_decl_model.py::test_decl_round_trips_and_refuses_wrong_kind_fields -q`
- SCION-CONNECTOR-CONTROL-R914.2: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_stdio_roots.py::test_missing_roots_refuse_and_empty_roots_pass_no_flag -q`
- SCION-CONNECTOR-CONTROL-R914.3: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_upstream_credentials.py::test_secrets_never_reach_logs_or_status_and_literals_refuse -q`
- SCION-CONNECTOR-CONTROL-R914.4: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_bridge_mode.py::test_token_file_is_private_and_removed_and_remote_address_refuses -q`
- SCION-CONNECTOR-CONTROL-R914.5: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_loader_wiring.py::test_namespaced_names_tags_and_collision_refusal -q`
- SCION-CONNECTOR-CONTROL-R914.6: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_toggles_and_strategy.py::test_disabled_skips_and_warn_isolates_a_failing_upstream -q`
- SCION-CONNECTOR-CONTROL-R914.7: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_activation.py::test_activation_is_per_session_lazy_and_capped -q`
- SCION-CONNECTOR-CONTROL-R914.8: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_pool_lifecycle.py::test_one_process_serves_many_sessions_and_restart_and_idle_reap -q`
- SCION-CONNECTOR-CONTROL-R914.9: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_upstream_health.py::test_inactive_is_not_failure_and_strategy_controls_readiness -q`
- SCION-CONNECTOR-CONTROL-R914.10: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_elicitation_passthrough.py::test_single_prompt_forwarded_and_decline_when_client_cannot_elicit -q`
- SCION-CONNECTOR-CONTROL-R914.11: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_drift.py::test_fingerprint_mismatch_reports_names_and_refuse_hides_namespace -q`
- SCION-CONNECTOR-CONTROL-R914.12: `PYTHONNOUSERSITE=1 uv run pytest tests/upstreams/test_certify_upstreams.py::test_certify_excludes_proxied_tools_from_pins -q`
- SCION-CONNECTOR-CONTROL-R916: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_served_contracts_resource_carries_the_declaration -q`
- SCION-CONNECTOR-CONTROL-R916.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_vocabulary_rolls_up_to_core_and_never_imports_it -q`
- SCION-CONNECTOR-CONTROL-R916.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_builders_normalise_lists_and_render_deterministic_individuals -q`
- SCION-CONNECTOR-CONTROL-R916.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_no_declaration_serves_no_contracts_resource -q`
- SCION-CONNECTOR-CONTROL-R916.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_each_missing_element_is_a_named_finding -q`
- SCION-CONNECTOR-CONTROL-R916.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_conformance_plugin.py::test_every_check_passes_for_a_conforming_connector -q`
- SCION-CONNECTOR-CONTROL-R916.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_self_check_flags_an_action_routed_tool -q`
- SCION-CONNECTOR-CONTROL-R916.7: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_complete_drafts_a_full_declaration_for_a_bare_connector -q`
- SCION-CONNECTOR-CONTROL-R916.8: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py::test_prompt_agent_definitions_are_validated -q`
- SCION-CONNECTOR-CONTROL-R916.9: `PYTHONNOUSERSITE=1 uv run pytest tests/test_surface_contracts.py -k upstream -q`
