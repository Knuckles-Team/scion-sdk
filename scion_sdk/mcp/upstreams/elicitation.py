"""Elicitation and confirmation pass through exactly once (R914.10).

The registry adds no confirmation to a proxied call and injects no
``confirm`` argument: the call reaches the upstream unchanged, and the
upstream's own annotations pass through. When the upstream asks the user
mid-call, the registry forwards the elicitation to the connected client
and returns the answer to the upstream. The forward runs on the pooled
client's session task, whose request context is from the call that first
connected the pool — so the forwarder sends on the session's connection
outbound, which outlives any single request. When the client has not
advertised elicitation, the registry answers the upstream with
``decline`` — never ``accept`` — and tags the unchanged result with the
``scion/elicitation`` metadata so a caller can see why.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.client.elicitation import ElicitationHandler, ElicitResult
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.proxy import ClientFactoryT, ProxyTool
from fastmcp.tools.base import ToolResult
from mcp_types import ElicitRequestFormParams, ElicitRequestURLParams

from scion_sdk.mcp.confirmation import elicitation_unavailable
from scion_sdk.mcp.upstreams.model import UPSTREAM_ELICITATION_UNAVAILABLE

__all__ = ["ELICITATION_META_KEY", "ForwardingProxyTool", "make_elicitation_forwarder"]

ELICITATION_META_KEY = "scion/elicitation"

_logger = logging.getLogger(__name__)


def make_elicitation_forwarder() -> ElicitationHandler[Any]:
    """The pooled client's elicitation handler: forward once, decline when unable."""

    async def forwarder(
        message: str,
        _response_type: Any,
        params: Any,
        request_context: Any,
    ) -> ElicitResult:
        try:
            context = get_context()
        except RuntimeError:
            return ElicitResult(action="decline")
        if elicitation_unavailable(context):
            _logger.info(
                "upstream elicitation declined: %s", UPSTREAM_ELICITATION_UNAVAILABLE
            )
            return ElicitResult(action="decline")
        try:
            session = context.session
            if isinstance(params, ElicitRequestFormParams):
                result = await session.elicit_form(
                    params.message, params.requested_schema
                )
            elif isinstance(params, ElicitRequestURLParams):
                if params.elicitation_id is None:
                    # The session API needs the id to route the answer; without
                    # it the forward is impossible, so decline like any other.
                    return ElicitResult(action="decline")
                result = await session.elicit_url(
                    params.message, params.url, params.elicitation_id
                )
            else:
                return ElicitResult(action="decline")
        except Exception:
            _logger.info(
                "upstream elicitation declined: %s", UPSTREAM_ELICITATION_UNAVAILABLE
            )
            return ElicitResult(action="decline")
        if result.action == "accept":
            return ElicitResult(action="accept", content=result.content)
        if result.action == "cancel":
            return ElicitResult(action="cancel")
        return ElicitResult(action="decline")

    return forwarder


class ForwardingProxyTool(ProxyTool):
    """A proxy tool that tags its result when the client cannot elicit.

    The upstream's own refusal comes back unchanged; only the
    :data:`ELICITATION_META_KEY` metadata is added, never a different error.
    """

    def __init__(self, client_factory: ClientFactoryT, **kwargs: Any) -> None:
        super().__init__(client_factory, **kwargs)

    @classmethod
    def from_proxy_tool(cls, tool: ProxyTool) -> ForwardingProxyTool:
        return cls(
            tool._client_factory,
            name=tool.name,
            title=tool.title,
            description=tool.description,
            parameters=tool.parameters,
            annotations=tool.annotations,
            output_schema=tool.output_schema,
            icons=tool.icons,
            meta=tool.meta,
            tags=tool.tags,
            execution=tool.execution,
        )

    async def run(self, arguments: dict[str, Any], context: Any = None) -> ToolResult:
        if context is None:
            try:
                context = get_context()
            except RuntimeError:
                context = None
        result = await super().run(arguments, context)
        if isinstance(context, Context) and elicitation_unavailable(context):
            result.meta = {**(result.meta or {}), ELICITATION_META_KEY: "unavailable"}
        return result
