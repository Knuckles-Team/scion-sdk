"""Every served tool result and error is redacted by default.

SCION-CONNECTOR-CONTROL-R025.7.1, R025.7.3 and R025.7.4. One middleware, installed by
``build_server``, passes each tool result and tool error through
``sanitize_tool_response`` before it is serialized. A tool that must return a
newly minted secret declares the field with ``secret_bearing_meta`` or types it
``SecretBearing``; only that tool's own result keeps those fields.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp import types as mt
from pydantic import Field, PlainSerializer

from scion_sdk.privacy import sanitize_tool_response

__all__ = [
    "REDACTED",
    "Redacted",
    "SecretBearing",
    "SecretBearingDeclarationError",
    "SecretRedactionMiddleware",
    "secret_bearing_meta",
    "stamp_secret_bearing",
]

REDACTED = "[REDACTED_SECRET]"
_META_RETURNS = "returnsSecret"
_META_FIELDS = "secretFields"
_logger = logging.getLogger("scion_sdk.mcp.tool_call")

type SecretBearing[T] = Annotated[
    T, Field(json_schema_extra={"x-secret-bearing": True})
]
type Redacted[T] = Annotated[
    T,
    PlainSerializer(lambda _value: REDACTED, return_type=str),
    Field(json_schema_extra={"x-redacted": True}),
]


class SecretBearingDeclarationError(ValueError):
    """A ``secret_bearing`` name is not a field of the tool's output schema."""

    def __init__(self, tool: str, field: str) -> None:
        self.tool = tool
        self.field = field
        super().__init__(
            f"tool {tool!r} declares secret_bearing field {field!r} "
            "that its output schema does not have"
        )


def secret_bearing_meta(*fields: str) -> dict[str, Any]:
    """Tool metadata declaring the fields of a newly minted secret."""
    if not fields:
        raise ValueError("name at least one secret-bearing field")
    return {_META_RETURNS: True, _META_FIELDS: list(fields)}


def _properties(schema: Any) -> dict[str, Any]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    return props if isinstance(props, dict) else {}


def _declared_fields(tool: Any) -> tuple[str, ...]:
    meta = getattr(tool, "meta", None) or {}
    declared = list(meta.get(_META_FIELDS, ())) if meta.get(_META_RETURNS) else []
    for name, prop in _properties(getattr(tool, "output_schema", None)).items():
        if (
            isinstance(prop, dict)
            and prop.get("x-secret-bearing")
            and name not in declared
        ):
            declared.append(name)
    return tuple(declared)


def stamp_secret_bearing(tools: Iterable[Any]) -> None:
    """Check each declaration against its output schema and stamp the properties."""
    for tool in tools:
        for field in _declared_fields(tool):
            props = _properties(getattr(tool, "output_schema", None))
            if field not in props:
                raise SecretBearingDeclarationError(tool.name, field)
            props[field]["x-secret-bearing"] = True


def _keep(value: Any, fields: tuple[str, ...]) -> Any:
    """Sanitize ``value`` but leave top-level ``fields`` of a mapping in place."""
    if not fields or not isinstance(value, dict):
        return sanitize_tool_response(value)
    kept = {name: value[name] for name in fields if name in value}
    clean = sanitize_tool_response({k: v for k, v in value.items() if k not in kept})
    return {**clean, **kept}


class SecretRedactionMiddleware(Middleware):
    """Redacts every served tool result and error; a connector cannot opt out."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Any,
    ) -> ToolResult:
        name = context.message.name
        arguments = context.message.arguments or {}
        try:
            result = await call_next(context)
        except Exception as error:
            message = sanitize_tool_response(str(error))
            _logger.warning(
                "tool_call_failed",
                extra={
                    "tool": name,
                    "arguments": sanitize_tool_response(arguments),
                    "error": message,
                },
            )
            raise ToolError(message) from None
        server = context.fastmcp_context.fastmcp if context.fastmcp_context else None
        tool = await server.get_tool(name) if server is not None else None
        fields = _declared_fields(tool) if tool is not None else ()
        structured = result.structured_content
        content = [
            block.model_copy(update={"text": _text(block.text, fields)})
            if isinstance(block, mt.TextContent)
            else block
            for block in result.content
        ]
        _logger.info(
            "tool_call",
            extra={
                "tool": name,
                "arguments": sanitize_tool_response(arguments),
                "result": sanitize_tool_response(structured),
            },
        )
        return ToolResult(
            content=content,
            structured_content=_keep(structured, fields)
            if structured is not None
            else None,
            meta=result.meta,
        )


def _text(text: str, fields: tuple[str, ...]) -> str:
    """Sanitize one text block; a JSON document is redacted by field name."""
    try:
        document = json.loads(text)
    except ValueError:
        return str(sanitize_tool_response(text))
    if isinstance(document, dict | list):
        return json.dumps(_keep(document, fields), separators=(",", ":"))
    return str(sanitize_tool_response(text))


def install(mcp: Any) -> None:
    """Install the middleware and validate declarations at server build."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        tools = pool.submit(asyncio.run, _list_tools(mcp)).result()
    stamp_secret_bearing(tools)
    mcp.add_middleware(SecretRedactionMiddleware())


async def _list_tools(mcp: Any) -> list[Any]:
    return list(await mcp.list_tools())
