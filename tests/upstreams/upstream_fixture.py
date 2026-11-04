"""The fixture upstream MCP server the R914 tests run as a stdio child.

The connector declares it with ``--fixture-kind`` plus the sandbox root flags;
this script strips those from ``sys.argv`` before the framework parses it and
records its process id in ``<write-root>/started-<kind>`` so a test can tell
whether, and with which process, the child started.

Kinds:
    tools   a stable tool set (save, read, compute, whoami)
    elicit  one tool that elicits mid-call and reports the outcome
    flaky   writes its marker, then exits before serving anything
    drift   like ``tools`` plus ``volatile`` whose schema changes when the
            file named by the ``CONTROL_FILE`` environment variable exists
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastmcp import Context, FastMCP
from fastmcp.server.context import CancelledElicitation, DeclinedElicitation

_KIND_FLAGS = ("--fixture-kind",)
_ROOT_FLAGS = ("--fixture-read-root", "--fixture-write-root")


def _partition_argv() -> tuple[str, dict[str, str]]:
    """``(kind, roots)``: fixture flags out, the rest left for the framework."""
    roots: dict[str, str] = {}
    kind = "tools"
    args = sys.argv[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _ROOT_FLAGS and index + 1 < len(args):
            roots[arg] = args[index + 1]
            index += 2
            continue
        if arg in _KIND_FLAGS and index + 1 < len(args):
            kind = args[index + 1]
            index += 2
            continue
        index += 1
    sys.argv = [sys.argv[0]]
    return kind, roots


def _build(kind: str, roots: dict[str, str]) -> None:
    write_root = roots.get("--fixture-write-root")
    if write_root:
        marker = Path(write_root) / f"started-{kind}"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(os.getpid()), encoding="utf-8")

    upstream = FastMCP(f"fixture-upstream-{kind}")

    if kind in ("tools", "drift"):

        @upstream.tool
        def save(what: str) -> str:
            """Persist a value."""
            return f"saved:{what}"

        @upstream.tool
        def read(item: str) -> str:
            """Read an item."""
            return f"read:{item}"

        @upstream.tool
        def whoami() -> str:
            """This process's id."""
            return str(os.getpid())

    if kind == "tools":

        @upstream.tool
        def compute(x: int) -> int:
            """Double a number."""
            return x * 2

    if kind == "elicit":

        @upstream.tool
        async def ask(question: str, ctx: Context) -> str:
            """Ask the user a question and report the outcome."""
            result = await ctx.elicit(question, bool)
            if isinstance(result, DeclinedElicitation):
                return "declined"
            if isinstance(result, CancelledElicitation):
                return "cancelled"
            return f"answer:{result.data}"

    if kind == "drift":
        control = os.environ.get("CONTROL_FILE")
        if control and Path(control).exists():

            @upstream.tool
            def volatile(text: str) -> str:
                """A tool whose schema drifted (string form)."""
                return text

        else:

            @upstream.tool
            def volatile(number: int) -> int:
                """A tool (integer form)."""
                return number

    if kind == "flaky":
        os._exit(0)

    upstream.run(transport="stdio")


if __name__ == "__main__":
    kind, roots = _partition_argv()
    _build(kind, roots)
