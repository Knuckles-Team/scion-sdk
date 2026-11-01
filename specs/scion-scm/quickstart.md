# SCION-SCM quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-SCM-R001.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_client.py::test_request_and_page_models_are_typed_and_frozen -q`
- SCION-SCM-R001.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_client.py::test_cache_is_bounded_and_digest_checked -q`
- SCION-SCM-R001.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_client_served.py::test_connector_tool_composes_the_client -q`
- SCION-SCM-R001.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_client.py::test_refusals_are_typed_and_zero_effect -q`
- SCION-SCM-R001.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_client.py::test_results_bind_provenance_without_secrets -q`
- SCION-SCM-R001.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_client_served.py::test_served_client_pages_and_conflict_refusal -q`
- SCION-SCM-R002: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_mount_served.py::test_served_mount_read_write_and_conflicting_flush -q`
- SCION-SCM-R002.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_mount.py::test_mount_state_and_entry_models_are_typed -q`
- SCION-SCM-R002.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_mount.py::test_reads_materialize_and_writes_accumulate -q`
- SCION-SCM-R002.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_mount.py::test_backend_selection_and_explicit_fuse_refusal -q`
- SCION-SCM-R002.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_source_mount.py::test_refusals_preserve_pending_changeset -q`
