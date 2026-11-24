"""The governed write-back state machine guard (SCION-GOVERNED-WRITEBACK-R007.3).

``execute_governed`` reads the proposal's state-machine descriptor and
refuses a stale record version or an illegal transition before any
external API call; a proposal without a descriptor passes unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from state_machine_fixtures import SUBMIT_AND_CANCEL

from scion_sdk.state_machine import StateTransitionRefused
from scion_sdk.writeback_sinks import core, run_writeback, runner
from scion_sdk.writeback_sinks.core import WritebackContext, WritebackResult
from scion_sdk.writeback_sinks.governed import (
    SinkGrant,
    StaleRecordRefused,
    execute_governed,
)

pytestmark = [
    pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R007.3"),
    pytest.mark.spec("SCION-GOVERNED-WRITEBACK-R007.5"),
]


class _Sink:
    """A sink whose run is the external API call the guard precedes."""

    domain = "req"
    enable_flag = "REQ_ENABLE_WRITE"

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def run(self, ctx: Any, ops: dict[str, Any], *, dry_run: bool) -> WritebackResult:
        self.calls.append(dry_run)
        result = WritebackResult(target=self.domain)
        result.created = 1
        return result


def _grant() -> SinkGrant:
    return SinkGrant(target="req", enable_flag="REQ_ENABLE_WRITE", write_enabled=True)


def _machine(
    *,
    state: str = "draft",
    event: str = "submit",
    facts: dict[str, Any] | None = None,
    record_version: str = "v1",
) -> dict[str, Any]:
    return {
        "descriptor": SUBMIT_AND_CANCEL,
        "state": state,
        "event": event,
        "facts": facts if facts is not None else {"line_count": 2},
        "record_version": record_version,
    }


def test_illegal_transition_and_stale_record_refuse_with_zero_api_calls(
    tmp_path: Any,
) -> None:
    # An illegal transition: the guard does not hold, so the refusal
    # names the machine, the state, the event and the failed guard.
    sink = _Sink()
    with pytest.raises(StateTransitionRefused) as raised:
        execute_governed(
            sink,
            WritebackContext(),
            {
                "expected_source_version": "v1",
                "_state_machine": _machine(facts={}),
            },
            grant=_grant(),
            dry_run=False,
            root=tmp_path,
        )
    refusal = raised.value
    assert refusal.code == "STATE_TRANSITION_REFUSED"
    assert (refusal.machine, refusal.from_state, refusal.event, refusal.guard) == (
        "Requisition",
        "draft",
        "submit",
        "has_lines",
    )
    assert sink.calls == []

    # An undeclared pair refuses without a guard name.
    sink = _Sink()
    with pytest.raises(StateTransitionRefused) as raised:
        execute_governed(
            sink,
            WritebackContext(),
            {
                "expected_source_version": "v1",
                "_state_machine": _machine(state="submitted", event="submit"),
            },
            grant=_grant(),
            dry_run=False,
            root=tmp_path,
        )
    assert (
        raised.value.code,
        raised.value.machine,
        raised.value.from_state,
        raised.value.event,
        raised.value.guard,
    ) == ("STATE_TRANSITION_REFUSED", "Requisition", "submitted", "submit", "")
    assert sink.calls == []

    # A stale record version refuses with STATE_STALE: the transition
    # would be legal at the version the proposal read, but the change
    # set targets another version.
    sink = _Sink()
    with pytest.raises(StaleRecordRefused) as raised:
        execute_governed(
            sink,
            WritebackContext(),
            {
                "expected_source_version": "v2",
                "_state_machine": _machine(),
            },
            grant=_grant(),
            dry_run=False,
            root=tmp_path,
        )
    refusal = raised.value
    assert refusal.code == "STATE_STALE"
    assert (refusal.machine, refusal.from_state, refusal.event) == (
        "Requisition",
        "draft",
        "submit",
    )
    assert (refusal.record_version, refusal.expected_version) == ("v1", "v2")
    assert sink.calls == []

    # The guard refuses a dry-run preview too.
    sink = _Sink()
    with pytest.raises(StaleRecordRefused):
        execute_governed(
            sink,
            WritebackContext(),
            {
                "expected_source_version": "v2",
                "_state_machine": _machine(),
            },
            grant=_grant(),
            dry_run=True,
            root=tmp_path,
        )
    assert sink.calls == []

    # An incomplete payload is refused before any call.
    for missing in ("descriptor", "state", "event", "record_version"):
        payload = _machine()
        del payload[missing]
        sink = _Sink()
        with pytest.raises(ValueError, match="state-machine"):
            execute_governed(
                sink,
                WritebackContext(),
                {
                    "expected_source_version": "v1",
                    "_state_machine": payload,
                },
                grant=_grant(),
                dry_run=False,
                root=tmp_path,
            )
        assert sink.calls == []

    # A legal transition at the matching version reaches the sink once.
    sink = _Sink()
    outcome = execute_governed(
        sink,
        WritebackContext(),
        {
            "expected_source_version": "v1",
            "_state_machine": _machine(),
        },
        grant=_grant(),
        dry_run=False,
        root=tmp_path,
    )
    assert sink.calls == [False]
    assert outcome.attempt is not None

    # A proposal without a descriptor passes unchanged.
    sink = _Sink()
    outcome = execute_governed(
        sink,
        WritebackContext(),
        {"creations": [{"name": "a"}]},
        grant=_grant(),
        dry_run=False,
        root=tmp_path,
    )
    assert sink.calls == [False]
    assert outcome.attempt is not None


def test_fixture_connector_blocks_illegal_external_transition(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The fixture connector's public entry point blocks an illegal external
    transition before the external API call."""
    sink = _Sink()
    core.register_sink(sink)
    monkeypatch.setattr(runner, "setting", lambda k, d=None, cast=None: True)
    try:
        # A submit from the submitted state refuses with zero external calls.
        out = run_writeback(
            "req",
            dry_run=False,
            expected_source_version="v1",
            _state_machine=_machine(state="submitted", event="submit"),
        )
        assert out["status"] == "refused"
        assert "STATE_TRANSITION_REFUSED" in out["reason"]
        assert sink.calls == []

        # A cancel from the draft state refuses with zero external calls.
        out = run_writeback(
            "req",
            dry_run=False,
            expected_source_version="v1",
            _state_machine=_machine(state="draft", event="cancel"),
        )
        assert out["status"] == "refused"
        assert "STATE_TRANSITION_REFUSED" in out["reason"]
        assert sink.calls == []

        # A legal submit at the matching version calls the API once.
        out = run_writeback(
            "req",
            dry_run=False,
            expected_source_version="v1",
            _state_machine=_machine(),
        )
        assert out["status"] == "completed"
        assert sink.calls == [False]
        assert out["governed_writeback"]["effect_status"] == "applied"
    finally:
        core._SINKS.pop("req", None)
