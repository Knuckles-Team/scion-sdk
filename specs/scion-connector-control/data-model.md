# SCION-CONNECTOR-CONTROL data model

This spec adds no new persisted type. Its rows describe the following in-memory shapes, all in existing modules.

- Risk class map (`scion_sdk/mcp/action_split.py`): a connector declares a risk class by tool name or per action. `split_tool_classes` returns the class of each split `<tool>_<action>` name: its own action's class, else its parent's single class. A parent listed as read-only passes only to an action whose verb reads.
- Unclassified set (`unclassified_tools`): every split name that gets no class. `assert_tools_classified` raises `Unclassified MCP tools prevent startup` while this set is not empty (SCION-CONNECTOR-CONTROL-R020.5).
- Tool surface (`scion_sdk/mcp/_tool_surface_actions.py`, `_tool_surface_base.py`): the registered tool names that the risk class map is checked against.
