# SCION-REPOSITORY-TRANSPORT quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-REPOSITORY-TRANSPORT-R001: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_transport.py::test_pages_are_combined_into_one_native_repository_batch -q`
- SCION-REPOSITORY-TRANSPORT-R003: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_refwalk_fastpath.py::test_distinct_commits_with_one_tree_walk_once -q`
- SCION-REPOSITORY-TRANSPORT-R004: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_transport.py::test_batch_limits_create_one_call_per_bounded_batch -q`
- SCION-REPOSITORY-TRANSPORT-R005: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_transport.py::test_batch_limits_create_one_call_per_bounded_batch -q`
- SCION-REPOSITORY-TRANSPORT-R006: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_refs.py::test_ref_walk_rejects_oversized_tree_page -q`
- SCION-REPOSITORY-TRANSPORT-R007: `PYTHONNOUSERSITE=1 uv run pytest tests/test_repository_refwalk_fastpath.py::test_local_blob_read_is_bound_to_selected_commit -q`
