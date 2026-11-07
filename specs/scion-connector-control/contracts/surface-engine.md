# Surface contract seam with the engine

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R916.3
Consumer: medulla MEDULLA-CQ-ORCH-R012.1
Consumer: medulla MEDULLA-CQ-ORCH-R012.2
Consumer: medulla MEDULLA-SRC-AUTH-R012

## Wire shape

The connector serves `ontology://<connector>/contracts.ttl` with MIME type `text/turtle`. It holds individuals only, with IRIs `urn:scion:<connector>:<kind>:<name>`. A `sc:ToolUseContract` names its task by `sc:forTask` and its steps by `kg:hasStep`. Each `sc:ToolStep` holds `sc:stepOrder`, `sc:callsTool`, `sc:param`, `sc:resultField` and `sc:refusal`, and steps chain by `kg:precedes`. A `sc:DataLocation` holds `sc:holdsClass`, `sc:instance`, `sc:store`, `sc:readTool`, `sc:routeParam` and `sc:federationKey`. Prompts and skills link by `sc:servesTask`, `sc:usesTool` and `sc:usesSkill`. The vocabulary is `scion_sdk/ontology/surface_contract.ttl`; every class extends a core class. The pack carries the resource as an `ontology` entry.

## Refusal codes

The SDK emits no refusal. A missing `connector_contracts.py` serves no resource, and the engine then has no contract for the connector.

## Authoritative types

[`scion_sdk/ontology/surface_contract.ttl`](https://github.com/Knuckles-Team/scion-sdk/blob/main/scion_sdk/ontology/surface_contract.ttl) and [`scion_sdk/surface_contracts/__init__.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/scion_sdk/surface_contracts/__init__.py).

## Test vectors

[`tests/test_surface_contracts.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/tests/test_surface_contracts.py)::`test_served_contracts_resource_carries_the_declaration`.
