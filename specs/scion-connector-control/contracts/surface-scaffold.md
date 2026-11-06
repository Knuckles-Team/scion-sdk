# Surface contract seam with the connector scaffold

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R916.2
Consumer: universal-skills US-MCP-BUILDER-R011.15

## Wire shape

The scaffold writes `<package>/connector_contracts.py` that imports the builders of `scion_sdk.surface_contracts` and declares `CONTRACTS`, `LINKS`, `LOCATIONS` and `ROLES` for the generated tools, prompts, skill and source. It writes one operator prompt and one business-role prompt with the `agent` object of `SCION-CONNECTOR-CONTROL-R916.8`. The generated package then passes the `surface_complete` conformance check (`SCION-CONNECTOR-CONTROL-R916.5`).

## Refusal codes

The `surface_complete` check fails with the sorted findings of `surface_findings`; the scaffold must produce none.

## Authoritative types

[`scion_sdk/surface_contracts.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/scion_sdk/surface_contracts.py).

## Test vectors

[`tests/test_surface_contracts.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/tests/test_surface_contracts.py).
