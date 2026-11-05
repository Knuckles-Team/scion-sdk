"""A declared upstream runs its calls through the artifact exchange (R914.13).

The loader reads the exchange settings once. For each declaration with an
``exchange`` binding it adds the exchange input tree as a read root and the
output tree as a write root, so a pooled child receives them at spawn. The
gate then serves an :class:`ExchangeProxyTool` for every tool of that
namespace. Without settings the binding is inert.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from fastmcp.server.providers.proxy import ProxyTool
from fastmcp.tools.base import ToolResult

from scion_sdk.exchange.config import ExchangeSettings, read_exchange_settings
from scion_sdk.exchange.inputs import ExchangeInputError
from scion_sdk.exchange.run import ExchangeCall, exchange_call
from scion_sdk.exchange.sinks import SinkBackends
from scion_sdk.exchange.staging import ExchangeRunError, input_tree, output_tree
from scion_sdk.mcp.upstreams.elicitation import ForwardingProxyTool
from scion_sdk.mcp.upstreams.model import SandboxRoots, UpstreamDecl

__all__ = [
    "ExchangeProxyTool",
    "ExchangeRuntime",
    "bind_exchange_roots",
    "load_exchange_runtime",
]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExchangeRuntime:
    """The settings and sink backends every bound upstream of one server shares."""

    settings: ExchangeSettings
    backends: SinkBackends
    connector: str


def load_exchange_runtime(
    declarations: Sequence[UpstreamDecl], connector: str
) -> ExchangeRuntime | None:
    """The runtime when any declaration is bound and settings exist, else ``None``."""
    if not any(decl.exchange is not None for decl in declarations):
        return None
    settings = read_exchange_settings()
    if settings is None:
        _logger.warning("upstream exchange bindings are inert: exchange not_configured")
        return None
    for tree in (input_tree(settings.staging_root), output_tree(settings.staging_root)):
        tree.mkdir(mode=0o700, parents=True, exist_ok=True)
    return ExchangeRuntime(settings, SinkBackends(settings), connector)


def bind_exchange_roots(
    decl: UpstreamDecl, runtime: ExchangeRuntime | None
) -> UpstreamDecl:
    """``decl`` with the exchange trees appended to its sandbox roots."""
    if runtime is None or decl.exchange is None or decl.sandbox is None:
        return decl
    root = runtime.settings.staging_root
    sandbox = decl.sandbox
    roots = SandboxRoots(
        read=(*sandbox.read, str(input_tree(root))),
        write=(*sandbox.write, str(output_tree(root))),
        read_flag=sandbox.read_flag,
        write_flag=sandbox.write_flag,
    )
    return decl.model_copy(update={"sandbox": roots})


def _request_meta(context: Any) -> Mapping[str, Any] | None:
    request = getattr(context, "request_context", None)
    meta = getattr(request, "meta", None)
    if meta is None:
        return None
    if isinstance(meta, Mapping):
        return meta
    dump = getattr(meta, "model_dump", None)
    return dump(by_alias=True) if callable(dump) else None


def _caller() -> str:
    try:
        from scion_sdk.identity import current_actor

        return current_actor().actor_id
    except Exception:
        return "anonymous"


class ExchangeProxyTool(ForwardingProxyTool):
    """A proxied tool whose calls stage inputs and publish outputs."""

    _exchange_runtime: ExchangeRuntime
    _exchange_decl: UpstreamDecl

    @classmethod
    def bind(
        cls, tool: ProxyTool, decl: UpstreamDecl, runtime: ExchangeRuntime
    ) -> ExchangeProxyTool:
        """Wrap one proxied tool of a bound upstream."""
        wrapped = cls.from_proxy_tool(tool)
        wrapped._exchange_runtime = runtime
        wrapped._exchange_decl = decl
        return wrapped

    def _spec(self) -> ExchangeCall:
        decl = self._exchange_decl
        prefix = f"{decl.effective_namespace}_"
        tool = self.name[len(prefix) :] if self.name.startswith(prefix) else self.name
        binding = decl.exchange
        path_args = binding.path_args if binding is not None else ()
        return ExchangeCall(
            namespace=decl.effective_namespace,
            tool=tool,
            connector=self._exchange_runtime.connector,
            path_args=path_args,
            caller=_caller(),
        )

    async def run(self, arguments: dict[str, Any], context: Any = None) -> ToolResult:
        if context is None:
            try:
                context = get_context()
            except RuntimeError:
                context = None
        runtime = self._exchange_runtime

        async def call(prepared: dict[str, Any]) -> ToolResult:
            return await ForwardingProxyTool.run(self, prepared, context)

        try:
            result: ToolResult = await exchange_call(
                arguments,
                call,
                settings=runtime.settings,
                backends=runtime.backends,
                spec=self._spec(),
                meta=_request_meta(context),
            )
            return result
        except (ExchangeInputError, ExchangeRunError) as exc:
            raise ToolError(str(exc)) from None
