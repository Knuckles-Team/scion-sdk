# SCION-OBSERVABILITY-FEEDS quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-OBSERVABILITY-FEEDS-R001.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_feeds.py::test_rejected_page_does_not_poison_event_identity -q`
- SCION-OBSERVABILITY-FEEDS-R001.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_feeds.py::test_event_identity_waits_for_worker_acceptance -q`
- SCION-OBSERVABILITY-FEEDS-R001.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_replay_checkpoint.py::test_restart_worker_process_against_real_graph -q`
- SCION-OBSERVABILITY-FEEDS-R001.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_runner.py::test_declared_event_feed_reaches_served_worker -q`
- SCION-OBSERVABILITY-FEEDS-R001.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_feed_registration.py::test_event_feed_entrypoints_require_certified_identity -q`
- SCION-OBSERVABILITY-FEEDS-R001.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_replay_checkpoint.py::test_exact_page_replay_keeps_distinct_same_time_event -q`
- SCION-OBSERVABILITY-FEEDS-R001.8: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_feeds.py::test_event_identity_capacity_preserves_known_entries -q`
- SCION-OBSERVABILITY-FEEDS-R001.7: `PYTHONNOUSERSITE=1 uv run pytest tests/test_event_feeds.py::test_bound_tenant_refuses_before_duplicate_classification -q`
- SCION-OBSERVABILITY-FEEDS-R002: `PYTHONNOUSERSITE=1 uv run pytest tests/test_host_profile_feed.py::test_host_profile_feed_ingests_observations -q`
