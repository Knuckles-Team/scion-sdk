"""Tool bodies and result coercion for :mod:`scion_sdk.mcp.method_tools`."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import InputRequiredResult
from pydantic import Field
from pydantic_core import to_jsonable_python

from scion_sdk.entitlements import require_entitled
from scion_sdk.mcp._method_params import _ParamShape
from scion_sdk.mcp.action_dispatch import parse_json_object
from scion_sdk.mcp.concurrency import invoke_client_method
from scion_sdk.mcp.context import confirmation_refusal, ctx_confirm_destructive

#: Explicit-confirmation argument of every destructive operation tool, for
#: clients that cannot elicit (SCION-CONNECTOR-CONTROL-R026.1).
_CONFIRM_PARAMETER = "confirm"
_CONFIRM_DESCRIPTION = (
    "Explicitly confirm this destructive operation. Needed only when the client "
    "cannot answer a confirmation prompt."
)

#: Named-instance argument advertised on every operation tool of a
#: connector that opts in (SCION-CENTRALIZED-CAPABILITIES-R050).
_INSTANCE_PARAMETER = "instance"
_INSTANCE_FALLBACK_PARAMETER = "upstream_instance"
_INSTANCE_DESCRIPTION = (
    "Name of the upstream instance to call; the connector's default "
    "instance when omitted."
)


def _fallback(value: Any) -> Any:
    status = getattr(value, "status_code", None)
    if isinstance(status, int):
        return {"status_code": status}
    return str(value)


def jsonable_result(result: Any) -> Any:
    """Default result coercion: JSON-ready data for any client return value.

    Pydantic models dump to JSON data, bytes encode as base64, and an
    unserializable object (a raw HTTP response) becomes its status code or text.
    """
    return to_jsonable_python(result, fallback=_fallback, bytes_mode="base64")


async def _resolve_client(get_client: Any) -> Any:
    client = get_client()
    if inspect.isawaitable(client):
        return await client
    return client


def _context_name(params: list[inspect.Parameter]) -> str:
    taken = {p.name for p in params}
    return "ctx" if "ctx" not in taken else "mcp_ctx"


def _instance_name(params: list[inspect.Parameter]) -> str:
    taken = {p.name for p in params}
    if _INSTANCE_PARAMETER not in taken:
        return _INSTANCE_PARAMETER
    return _INSTANCE_FALLBACK_PARAMETER


async def _invoke(
    method_name: str,
    get_client: Any,
    *,
    kwargs: dict[str, Any],
    ctx: Any,
    destructive: bool,
    coercer: Callable[[Any], Any],
    confirm: bool = False,
    instance: str | None = None,
    instances: Any = None,
) -> Any:
    if destructive:
        confirmation = await ctx_confirm_destructive(
            ctx,
            method_name,
            confirmation_arguments=kwargs,
            explicit_confirm=confirm,
        )
        if isinstance(confirmation, InputRequiredResult):
            return confirmation
        if confirmation is not True:
            return confirmation_refusal(ctx, method_name)
    if instances is not None and instance is not None:
        # The caller's entitlement check applies per instance, before any
        # instance client is built (SCION-CENTRALIZED-CAPABILITIES-R050).
        require_entitled(instances.namespace, instance)
        client = instances.client_for(instance)
    else:
        client = await _resolve_client(get_client)
    result = await invoke_client_method(getattr(client, method_name), **kwargs)
    coerced = coercer(result)
    return await coerced if inspect.isawaitable(coerced) else coerced


def _typed_tool(
    method_name: str,
    get_client: Any,
    shape: _ParamShape,
    *,
    destructive: bool,
    coercer: Callable[[Any], Any],
    returns: Any = Any,
    instances: Any = None,
) -> Callable[..., Any]:
    """A tool whose signature mirrors the operation's typed parameters."""
    params = shape.params
    ctx_name = _context_name(params)
    inst_name = _instance_name(params) if instances is not None else None
    declared = {p.name for p in params}
    with_confirm = destructive and _CONFIRM_PARAMETER not in declared

    async def _tool(**arguments: Any) -> Any:
        ctx = arguments.pop(ctx_name, None)
        confirm = (
            bool(arguments.pop(_CONFIRM_PARAMETER, False)) if with_confirm else False
        )
        instance = arguments.pop(inst_name, None) if inst_name else None
        extra = parse_json_object(arguments.pop("params_json", None))
        kwargs = {
            k: v
            for k, v in arguments.items()
            if k in declared and v is not None and v is not inspect.Parameter.empty
        }
        kwargs.update({k: v for k, v in extra.items() if v is not None})
        return await _invoke(
            method_name,
            get_client,
            kwargs=kwargs,
            ctx=ctx,
            destructive=destructive,
            coercer=coercer,
            confirm=confirm,
            instance=instance,
            instances=instances,
        )

    ordered = [p for p in params if p.default is inspect.Parameter.empty] + [
        p for p in params if p.default is not inspect.Parameter.empty
    ]
    if shape.extra_json:
        ordered.append(
            inspect.Parameter(
                "params_json",
                inspect.Parameter.KEYWORD_ONLY,
                default="{}",
                annotation=str,
            )
        )
    if with_confirm:
        ordered.append(
            inspect.Parameter(
                _CONFIRM_PARAMETER,
                inspect.Parameter.KEYWORD_ONLY,
                default=False,
                annotation=Annotated[bool, Field(description=_CONFIRM_DESCRIPTION)],
            )
        )
    if inst_name:
        ordered.append(
            inspect.Parameter(
                inst_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=Annotated[
                    str | None, Field(description=_INSTANCE_DESCRIPTION)
                ],
            )
        )
    ordered.append(
        inspect.Parameter(
            ctx_name,
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=Context | None,
        )
    )
    _tool.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        ordered, return_annotation=returns
    )
    _tool.__annotations__ = {p.name: p.annotation for p in ordered} | {
        "return": returns
    }
    return _tool


def _untyped_tool(
    method_name: str,
    get_client: Any,
    *,
    destructive: bool,
    coercer: Callable[[Any], Any],
    instances: Any = None,
) -> Callable[..., Any]:
    """A ``params_json`` tool for an operation with no typed surface."""

    async def _tool(
        params_json: str = Field(
            default="{}", description="JSON object of arguments for this operation."
        ),
        confirm: bool = Field(default=False, description=_CONFIRM_DESCRIPTION),
        ctx: Context | None = None,
        instance: Annotated[
            str | None, Field(description=_INSTANCE_DESCRIPTION)
        ] = None,
    ) -> Any:
        kwargs = parse_json_object(params_json)
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return await _invoke(
            method_name,
            get_client,
            kwargs=kwargs,
            ctx=ctx,
            destructive=destructive,
            coercer=coercer,
            confirm=destructive and confirm is True,
            instance=instance if instances is not None else None,
            instances=instances,
        )

    signature = inspect.signature(_tool)
    if not destructive:
        signature = signature.replace(
            parameters=[p for p in signature.parameters.values() if p.name != "confirm"]
        )
        _tool.__annotations__.pop("confirm", None)
    if instances is None:
        signature = signature.replace(
            parameters=[
                p
                for p in signature.parameters.values()
                if p.name != _INSTANCE_PARAMETER
            ]
        )
        _tool.__annotations__.pop(_INSTANCE_PARAMETER, None)
    _tool.__signature__ = signature  # type: ignore[attr-defined]
    return _tool
