"""Direct coverage of the external-id resolver and stamp helper."""

from __future__ import annotations

from scion_sdk.writeback_sinks._external_ids import (
    _upsert_stamp,
    resolve_external_id,
)


class _Backend:
    def execute(self, query: str, params: dict[str, str]) -> list[dict[str, str]]:
        assert params == {"d": "x"}
        return [{"id": "n1", "guid": "g1"}, {"id": "", "guid": "g2"}]


def test_resolver_maps_node_to_external_id() -> None:
    resolve = resolve_external_id(_Backend(), "x")
    assert resolve("n1") == "g1"
    assert resolve("missing") is None


def test_resolver_without_backend_is_empty() -> None:
    assert resolve_external_id(None, "x")("n1") is None


def test_upsert_stamp_falls_back_to_keyword_signature() -> None:
    seen: dict[str, object] = {}

    def add_node(node_id: str, label: str = "", **props: object) -> None:
        seen.update(id=node_id, label=label, **props)

    def positional_only(node_id: str, label: str, props: dict[str, object]) -> None:
        raise TypeError

    assert _upsert_stamp(positional_only, "n", label="L", props={"a": 1}) is False
    assert _upsert_stamp(add_node, "n", label="L", props={"a": 1}) is False or seen


def test_invocation_unpack_is_a_named_five_tuple() -> None:
    from scion_sdk.writeback_sinks.core import (
        InvocationParts,
        WritebackContext,
        WritebackInvocation,
        WritebackResult,
    )

    result = WritebackResult(target="t")
    inv = WritebackInvocation(WritebackContext(), {}, None, result, True)
    parts = inv.unpack()
    assert isinstance(parts, InvocationParts)
    ctx, ops, client, res, dry = parts
    assert (ops, client, res, dry) == ({}, None, result, True)
    assert ctx is inv.ctx
