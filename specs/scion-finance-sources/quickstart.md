# SCION-FINANCE-SOURCES quickstart

Run the served proof of the rows below from a fresh checkout after `uv sync`. Each command is a scenario that exercises the row through its production entry point.

- SCION-FINANCE-SOURCES-R001.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_fred_series.py::test_nonfinite_values_are_rejected -q`
- SCION-FINANCE-SOURCES-R001.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_market_source.py::test_served_market_sources_reach_sink -q`
- SCION-FINANCE-SOURCES-R001.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_central_bank_calendar.py::test_same_vintage_distinct_announcements_refuse_identity_collision -q`
- SCION-FINANCE-SOURCES-R001.4: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_fred_series.py::test_observation_page_requires_an_array -q`
- SCION-FINANCE-SOURCES-R001.5: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_fred_series.py::test_fred_observation_identity_requires_canonical_dates -q`
- SCION-FINANCE-SOURCES-R001.6: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_fred_series.py::test_fred_page_refuses_duplicate_observation_identities -q`
- SCION-FINANCE-SOURCES-R002.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_stock_image.py::test_stock_adapter_returns_generated_nonofficial_image -q`
- SCION-FINANCE-SOURCES-R002.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_records.py::test_market_records_reject_nonfinite_values -q`
- SCION-FINANCE-SOURCES-R002.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_alpaca_stocks.py::test_malformed_page_is_rejected -q`
- SCION-FINANCE-SOURCES-R003.1: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_multi_asset_source.py::test_served_sources_preserve_fx_direction_futures_and_staleness -q`
- SCION-FINANCE-SOURCES-R003.2: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_records.py::test_multi_asset_refuses_nonfinite_prices -q`
- SCION-FINANCE-SOURCES-R003.3: `PYTHONNOUSERSITE=1 uv run pytest tests/test_finance_records.py::test_fx_currency_codes_are_canonical_and_distinct -q`
