"""Successor tool parity (SCION-CONNECTOR-CONTROL-R020.4).

A successor release keeps every predecessor tool: kept, split into
``<tool>_<action>`` tools, or covered by a per-operation tool that makes the
same API call. A lost tool is refused and named; a remaining action-dispatch
tool fails the release. The certify gate (``scion_sdk/certify/cli.py``) feeds
kept and split outcomes from the two live ``tools/list`` results; ``covered_by``
arrives as caller input, so the gate itself checks no per-operation coverage.
"""

from __future__ import annotations

import pytest

from scion_sdk.certify.successor_parity import (
    SuccessorToolParity,
    compare_successor_tools,
)

pytestmark = pytest.mark.spec("SCION-CONNECTOR-CONTROL-R020.4")


def test_kept_split_covered_and_lost_tool_refused() -> None:
    result = compare_successor_tools(
        ["kept", "router", "legacy_op", "gone"],
        ["kept", "router_get", "router_put", "op_service"],
        covered_by={"legacy_op": "op_service"},
    )
    assert result.kept == ("kept",)
    assert result.split == {"router": ("router_get", "router_put")}
    assert result.covered == {"legacy_op": "op_service"}
    assert result.lost == ("gone",)
    assert not result.accepted


def test_kept_split_and_covered_tools_are_accepted() -> None:
    result = compare_successor_tools(
        ["a", "router", "old"],
        ["a", "router_get", "router_put", "new_op"],
        covered_by={"old": "new_op"},
    )
    assert result.kept == ("a",)
    assert result.split == {"router": ("router_get", "router_put")}
    assert result.covered == {"old": "new_op"}
    assert result.accepted


def test_one_lost_tool_is_refused() -> None:
    result = compare_successor_tools(["a", "b"], ["a"])
    assert result.lost == ("b",)
    assert not result.accepted


def test_a_remaining_dispatch_tool_is_refused() -> None:
    result = compare_successor_tools(["r"], ["r"], dispatch_tools=["r"])
    assert result.dispatch_remaining == ("r",)
    assert not result.accepted


def test_a_cover_naming_an_unserved_tool_is_lost() -> None:
    assert compare_successor_tools(["x"], [], covered_by={"x": "y"}).lost == ("x",)


def test_duplicate_names_raise() -> None:
    assert SuccessorToolParity((), {}, {}, (), ()).accepted
    with pytest.raises(ValueError):
        compare_successor_tools(["a", "a"], ["a"])
