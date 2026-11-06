# Surface contract seam with the connector surface gate

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R916.2
Consumer: pipelines PIPE-GATES-R014.5
Consumer: pipelines PIPE-GATES-R014.6
Consumer: pipelines PIPE-GATES-R014.7

## Wire shape

A connector package holds `<package>/connector_contracts.py`. It binds the module-level tuples `CONTRACTS`, `LINKS`, `LOCATIONS` and `ROLES` to calls of `ToolUseContract(task, steps, description)`, `ToolStep(tool, params, result, refusals)`, `Refusal(code, when)`, `SurfaceLink(task, tools, prompts, skills)`, `DataLocation(name, holds, instance, read_tool, store, route_param, federation_key)` and `BusinessRole(name, tasks, description)`. Positional arguments follow that field order. The gate reads the calls statically and imports no SDK module. A prompt `<package>/prompts/<name>.json` carries the `agent` object of `SCION-CONNECTOR-CONTROL-R916.8`.

## Refusal codes

The SDK refuses nothing at declaration time. `surface_findings` (`SCION-CONNECTOR-CONTROL-R916.4`) and the gate name the same gaps in their own text: no contract, a step without a result field, no refusal, an unlinked prompt or skill, no data location, and an incomplete agent definition.

## Authoritative types

[`scion_sdk/surface_contracts.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/scion_sdk/surface_contracts.py).

## Test vectors

[`tests/connector_dist.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/tests/connector_dist.py) holds the complete fixture declaration.
