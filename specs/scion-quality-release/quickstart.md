# SCION-QUALITY-RELEASE quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-QUALITY-RELEASE-R005.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_conformance_plugin.py::test_every_check_passes_for_a_conforming_connector -q`
