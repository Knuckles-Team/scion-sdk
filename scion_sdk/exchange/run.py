"""One exchanged call: stage, call, publish, link and record (SCION-CENTRALIZED-CAPABILITIES-R043).

:func:`exchange_call` wraps one tool call. It opens the run staging, stages
every input reference, rewrites relative path arguments into the run's output
directory, runs the call once, then publishes the outputs, attaches links and
records lineage. The served caller is the upstream proxy of
``SCION-CONNECTOR-CONTROL-R914.13``; a connector tool body may call it too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import anyio

from scion_sdk.exchange.config import ExchangeSettings
from scion_sdk.exchange.inputs import (
    EXCHANGE_INPUT_FAILED,
    ExchangeInputError,
    is_input_reference,
    resolve_input,
)
from scion_sdk.exchange.lineage import StagedInput, record_lineage
from scion_sdk.exchange.publish import PublishContext, digest_file, publish_outputs
from scion_sdk.exchange.results import attach_artifacts
from scion_sdk.exchange.sinks import SinkBackends
from scion_sdk.exchange.staging import RunStaging, run_id_from_meta

__all__ = ["ExchangeCall", "exchange_call", "prepare_arguments"]


@dataclass(frozen=True)
class ExchangeCall:
    """Who calls what: the namespace, tool, caller and path arguments of one call."""

    namespace: str
    tool: str
    connector: str
    path_args: tuple[str, ...] = ("path", "dir", "out", "out_dir")
    caller: str = "anonymous"


def _output_path(value: str, staging: RunStaging) -> str:
    relative = PurePosixPath(value.replace("\\", "/"))
    if ".." in relative.parts:
        raise ExchangeInputError(
            EXCHANGE_INPUT_FAILED, "a path argument leaves the run"
        )
    target = staging.output_dir.joinpath(*relative.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


class _Stager:
    def __init__(
        self,
        staging: RunStaging,
        settings: ExchangeSettings,
        backends: SinkBackends,
        transport: Any,
    ) -> None:
        self.staging, self.settings, self.backends, self.transport = (
            staging,
            settings,
            backends,
            transport,
        )
        self.inputs: list[StagedInput] = []

    def value(self, value: Any) -> Any:
        if not is_input_reference(value):
            return value
        staged = resolve_input(
            value, self.staging, self.settings, self.backends, transport=self.transport
        )
        if staged is None:  # pragma: no cover - guarded by is_input_reference
            return value
        self.inputs.append(
            StagedInput(
                str(value), staged.name, digest_file(staged), staged.stat().st_size
            )
        )
        return str(staged)


def prepare_arguments(
    arguments: Mapping[str, Any],
    staging: RunStaging,
    stager: _Stager,
    path_args: Sequence[str],
) -> dict[str, Any]:
    """Stage input references and move relative path arguments into the run."""
    prepared: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            prepared[key] = [stager.value(v) for v in value]
            continue
        staged = stager.value(value)
        if staged is not value:
            prepared[key] = staged
        elif (
            key in path_args
            and isinstance(value, str)
            and value
            and not Path(value).is_absolute()
        ):
            prepared[key] = _output_path(value, staging)
        else:
            prepared[key] = value
    return prepared


async def exchange_call(
    arguments: Mapping[str, Any],
    call: Callable[[dict[str, Any]], Awaitable[Any]],
    *,
    settings: ExchangeSettings,
    backends: SinkBackends,
    spec: ExchangeCall,
    meta: Mapping[str, Any] | None = None,
    transport: Any = None,
) -> Any:
    """Run ``call`` once inside the exchange and return its augmented result.

    Raises:
        ExchangeRunError: the run ID refuses, before the call.
        ExchangeInputError: an input refuses, before the call.
    """
    run_id = run_id_from_meta(meta)
    staging = await anyio.to_thread.run_sync(
        RunStaging.open, settings, spec.namespace, run_id
    )
    stager = _Stager(staging, settings, backends, transport)
    prepared = await anyio.to_thread.run_sync(
        prepare_arguments, arguments, staging, stager, spec.path_args
    )
    result = await call(prepared)
    context = PublishContext(
        namespace=spec.namespace, tool=spec.tool, caller=spec.caller
    )
    report = await anyio.to_thread.run_sync(
        publish_outputs, staging, settings, backends, context
    )
    codes: list[str] = []
    if settings.lineage and report.artifacts:
        code = await record_lineage(
            report, stager.inputs, context, connector=spec.connector
        )
        if code:
            codes.append(code)
    return attach_artifacts(result, report, codes)
