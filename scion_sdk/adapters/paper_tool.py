"""Register host-authenticated paper search through the MCP tool surface."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from scion_sdk.extras import require_mcp_extra

require_mcp_extra("scion_sdk.adapters.paper_tool")

from fastmcp import Context  # noqa: E402

from scion_sdk.adapters.paper_download import (  # noqa: E402
    DocumentRefusal,
    DocumentRequest,
    PaperDocumentService,
)
from scion_sdk.adapters.paper_maintenance import (  # noqa: E402
    MaintenanceRefusal,
    PaperMaintenanceOrder,
    execute_paper_maintenance,
)
from scion_sdk.adapters.paper_models import (  # noqa: E402
    PaperQuery,
    PaperSearchResult,
)
from scion_sdk.adapters.paper_search import PaperSearchService  # noqa: E402
from scion_sdk.ports.sink import Sink  # noqa: E402

__all__ = [
    "register_paper_download_tool",
    "register_paper_maintenance_tool",
    "register_paper_search_tool",
]


def register_paper_search_tool(server: Any, service: PaperSearchService) -> None:
    """Register an operation whose host authenticates each request context."""

    async def paper_search(query: PaperQuery, ctx: Context) -> PaperSearchResult:
        """Search the selected independent paper providers."""
        return await service.search(query, ctx)

    server.tool(name="paper_search", annotations={"readOnlyHint": True})(paper_search)


def register_paper_download_tool(server: Any, service: PaperDocumentService) -> None:
    """Register a transfer with host-supplied tenant and destination admission."""

    async def paper_download(request: DocumentRequest, ctx: Context) -> dict[str, Any]:
        """Save one complete authorized document or return a closed refusal."""
        try:
            receipt = await service.download(request, ctx)
            return {"receipt": receipt.model_dump(mode="json"), "reason": None}
        except DocumentRefusal as error:
            return {"receipt": None, "reason": error.reason}

    server.tool(name="paper_download", annotations={"readOnlyHint": False})(
        paper_download
    )


def register_paper_maintenance_tool(
    server: Any,
    sink: Sink,
    *,
    connector: str,
    stream: str,
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
) -> None:
    """Register the destructive order tool; nothing runs unless it is called."""

    async def paper_maintenance_apply(order: PaperMaintenanceOrder) -> dict[str, Any]:
        """Apply one admitted maintenance order as engine withdrawals."""
        try:
            receipt = await execute_paper_maintenance(
                order, sink, connector, stream, clock_ms()
            )
        except MaintenanceRefusal as refusal:
            return {"receipt": None, "reason": refusal.code.value}
        return {"receipt": receipt.model_dump(mode="json"), "reason": None}

    server.tool(
        name="paper_maintenance_apply",
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )(paper_maintenance_apply)
