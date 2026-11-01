# SCION-SOURCE-INGEST quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-SOURCE-INGEST-R002: `PYTHONNOUSERSITE=1 uv run pytest tests/test_schema_drift.py::test_classifier_contract_covers_representative_changes_and_quarantine -q`
- SCION-SOURCE-INGEST-R003: `PYTHONNOUSERSITE=1 uv run pytest tests/test_schema_repair.py::test_a_recorded_proposal_pauses_the_stream_before_any_extract_or_submit -q`
- SCION-SOURCE-INGEST-R005.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_paper_contract.py::test_query_continuations_bind_exact_request_and_limits -q`
- SCION-SOURCE-INGEST-R005.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_paper_source_served.py::test_scholarx_tools_register_and_sync -q`
- SCION-SOURCE-INGEST-R005.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_paper_contract.py::test_provenance_conflicts_redaction_and_identity_stable_across_retries -q`
- SCION-SOURCE-INGEST-R005.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_paper_source_served.py::test_served_search_download_and_sync -q`
- SCION-SOURCE-INGEST-R006.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_vendor_extractors_uptime_kuma.py::test_extract_returns_a_change_set_of_entities_for_dict_shaped_monitors -q`
- SCION-SOURCE-INGEST-R006.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_vendor_extractors_ansible.py::test_extract_returns_a_change_set_of_server_entities -q`
- SCION-SOURCE-INGEST-R006.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_vendor_extractors_caddy.py::test_extract_returns_a_change_set_of_service_entities -q`
- SCION-SOURCE-INGEST-R006.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_vendor_extractors_home_assistant.py::test_extract_returns_a_change_set_of_configuration_item_entities -q`
- SCION-SOURCE-INGEST-R006.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_vendor_extractors_archimate.py::test_extract_returns_typed_elements_and_relationships -q`
- SCION-SOURCE-INGEST-R006.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_vendor_extractors_kafka.py::test_extract_returns_topic_and_service_entities -q`
