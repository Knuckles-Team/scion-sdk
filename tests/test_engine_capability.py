"""The pinned-engine capability guard names the method it refuses."""

from __future__ import annotations

import pytest

from scion_sdk.engine_capability import (
    ENGINE_CAPABILITIES,
    EngineCapabilityUnavailable,
    engine_capability_present,
    require_engine_capability,
)
from scion_sdk.ontology.instance_profile import InstanceProfileDraft


def test_guard_refuses_exactly_what_the_pin_lacks() -> None:
    for method in ENGINE_CAPABILITIES:
        if engine_capability_present(method):
            require_engine_capability(method)
        else:
            with pytest.raises(EngineCapabilityUnavailable) as caught:
                require_engine_capability(method)
            assert caught.value.method == method and method in str(caught.value)


def test_unknown_method_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown engine capability"):
        engine_capability_present("NotAMethod")


def test_empty_draft_has_the_three_wire_sections() -> None:
    assert InstanceProfileDraft().wire() == {
        "classes": [],
        "datatype_properties": [],
        "object_properties": [],
    }
