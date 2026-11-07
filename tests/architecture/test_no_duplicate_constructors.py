"""Each former duplicate copy is served by one parameterised function."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from scion_sdk.http.client import build_http_client
from scion_sdk.http.options import HttpClientOptions
from scion_sdk.writeback_sinks.core import WritebackClientMixin, run_sink

pytestmark = pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R045")

PACKAGE = Path(__file__).resolve().parents[2] / "scion_sdk"
RETIRED = {
    "create_http_client",
    "create_async_http_client",
    "configure_oauth_proxy",
    "configure_oidc_proxy",
}
SINK_MODULES = ("servicenow", "erpnext", "archimate", "salesforce")


def _defined_names(path: Path) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_one_function_serves_each_former_copy() -> None:
    defined: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        defined |= _defined_names(path) & RETIRED
    assert defined == set()
    for module in SINK_MODULES:
        path = PACKAGE / "writeback_sinks" / "sinks" / f"{module}.py"
        assert "_run" not in _defined_names(path)


def test_http_factory_serves_both_clients() -> None:
    options = HttpClientOptions(base_url="https://example.test")
    with build_http_client(options) as sync_client:
        assert isinstance(sync_client, httpx.Client)
    assert isinstance(build_http_client(options, async_=True), httpx.AsyncClient)


def test_sink_runner_applies_each_pass_in_order() -> None:
    seen: list[tuple[str, str]] = []

    class Ctx:
        def resolver(self, domain: str):
            return domain

    class Sink(WritebackClientMixin):
        domain = "demo"
        passes = (("creations", "_make"), ("retirements", "_drop"))

        def _make(self, ctx, client, result, item, resolve, *, dry_run):
            seen.append(("make", item))

        def _drop(self, ctx, client, result, item, resolve, *, dry_run):
            seen.append(("drop", item))

    class Invocation:
        def unpack(self):
            ops = {"retirements": ["r"], "creations": ["a", "b"]}
            return Ctx(), ops, None, "result", False

    assert run_sink(Sink(), Invocation(), Sink.passes) == "result"
    assert seen == [("make", "a"), ("make", "b"), ("drop", "r")]
