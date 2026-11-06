# SCION-CONNECTOR-CONTROL data model

This spec adds no new persisted type. Its rows describe the following in-memory shapes, all in existing modules.

- Risk class map (`scion_sdk/mcp/action_split.py`): a connector declares a risk class by tool name or per action. `split_tool_classes` returns the class of each split `<tool>_<action>` name: its own action's class, else its parent's single class. A parent listed as read-only passes only to an action whose verb reads.
- Unclassified set (`unclassified_tools`): every split name that gets no class. `assert_tools_classified` raises `Unclassified MCP tools prevent startup` while this set is not empty (SCION-CONNECTOR-CONTROL-R020.5).
- Tool surface (`scion_sdk/mcp/_tool_surface_actions.py`, `_tool_surface_base.py`): the registered tool names that the risk class map is checked against.
- Upstream declaration (`scion_sdk/mcp/upstreams/model.py`, SCION-CONNECTOR-CONTROL-R914.1): frozen `UpstreamDecl`, `SandboxRoots` and `BridgeDecl`. A connector declares them in code on `Connector.upstreams`, and an operator overlay file merges by name. No upstream declaration is persisted by the SDK.
- Upstream status (`scion_sdk/mcp/upstreams/model.py`, SCION-CONNECTOR-CONTROL-R914.9): `UpstreamStatus` is in memory and is rebuilt from the live upstream. It holds error codes and never upstream error text.
- Surface declaration (`scion_sdk/surface_contracts.py`, SCION-CONNECTOR-CONTROL-R916.2): frozen `Refusal`, `ToolStep`, `ToolUseContract`, `SurfaceLink`, `DataLocation`, `BusinessRole` and `SurfaceDeclaration`. A connector declares them in `connector_contracts.py`; the SDK persists none.
- Surface vocabulary (`scion_sdk/ontology/surface_contract.ttl`, SCION-CONNECTOR-CONTROL-R916.1): the ontology classes `sc:ToolUseContract`, `sc:ToolStep`, `sc:Refusal`, `sc:DataLocation` and `sc:BusinessRole`, each a subclass of a core class.
- Prompt agent definition (SCION-CONNECTOR-CONTROL-R916.8): the `agent` object of a prompt JSON, carried in the served prompt meta.
- Connector lifecycle (`scion_sdk/connector_lifecycle.py`, SCION-CONNECTOR-CONTROL-R917.1): `LifecycleState`, `LifecycleEvent` and the edge table. `RunnerHealth` keeps `lifecycle_state` as the one stored field. The SDK persists nothing.
- Circuit breaker (`scion_sdk/resilience.py`, SCION-CONNECTOR-CONTROL-R918.1): `CircuitBreaker` with the one field `state`, in memory.
