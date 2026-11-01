# SCION-CENTRALIZED-CAPABILITIES quickstart

Run the proofs from the repository root. None needs a network.

## Index test (every row)

```bash
uv run pytest tests/test_centralized_capability_index.py tests/test_centralized_extras.py
```

Expected: every module path, extra, script and owner row of the index resolves.

## Pagination and redaction rows (R004.1 to R004.8, R006.1 to R006.5)

```bash
uv run pytest tests/httpsupport tests/test_governed_problem_redaction.py tests/test_http_diagnostic_origin.py tests/test_graphql_failure_redaction.py tests/test_graphql_client.py
```

Expected: unsafe pages are refused before a fetch and no error holds a secret.

## A2A storage and certify evidence (R017.1, R017.2, R041, planned)

```bash
uv run pytest tests/test_a2a_protocol.py tests/test_certify_surface.py
```

Expected once built: `tasks/list` serves durable storage and the certify report reaches a loopback engine.
