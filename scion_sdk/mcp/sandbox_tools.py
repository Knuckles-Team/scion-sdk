"""Register the sandbox allocation tools (SCION-CONNECTOR-CONTROL-R051.3).

``sandbox_provision`` is the tool a run admission calls through ``fleet.call``;
``sandbox_reconcile`` is its independent read-back and ``sandbox_release`` frees
an allocation. A refusal is a tool error carrying only the closed code.
"""

from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError

from scion_sdk.ports.sandbox import (
    ProvisionRequest,
    SandboxProvisioner,
    SandboxRefused,
)

__all__ = ["register_sandbox_tools"]


def register_sandbox_tools(
    mcp: Any, provisioner: SandboxProvisioner
) -> tuple[str, ...]:
    """Register the three tools on ``mcp``; return their names."""

    async def sandbox_provision(request: dict[str, Any]) -> dict[str, Any]:
        """Allocate sandbox instances once per operation id."""
        try:
            parsed = ProvisionRequest.model_validate(request, strict=False)
            result = await provisioner.provision(parsed)
        except SandboxRefused as refusal:
            raise ToolError(refusal.code) from None
        except ValueError:
            raise ToolError("invalid_request") from None
        return result.model_dump(mode="json")

    async def sandbox_reconcile(operation_id: str) -> dict[str, Any] | None:
        """Read back the allocation recorded for an operation id."""
        try:
            result = await provisioner.reconcile(operation_id)
        except SandboxRefused as refusal:
            raise ToolError(refusal.code) from None
        return None if result is None else result.model_dump(mode="json")

    async def sandbox_release(allocation_id: str) -> dict[str, Any]:
        """Release one allocation."""
        return (await provisioner.release(allocation_id)).model_dump(mode="json")

    for function in (sandbox_provision, sandbox_reconcile, sandbox_release):
        mcp.tool(name=function.__name__)(function)
    return ("sandbox_provision", "sandbox_reconcile", "sandbox_release")
