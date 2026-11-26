"""One capability contract unifies a tool and a skill resource (R913)."""

from __future__ import annotations

import pytest

from scion_sdk.contracts import (
    CapabilityContract,
    CapabilityContractError,
    FrontendContribution,
)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R913")
def test_contract_unifies_tool_and_skill_and_contribution_needs_read_model() -> None:
    for kind in ("tool", "skill_resource"):
        assert CapabilityContract(id=f"c-{kind}", kind=kind).kind == kind
    with pytest.raises(CapabilityContractError) as unknown:
        CapabilityContract(id="x", kind="widget")
    assert unknown.value.code == "CAPABILITY_KIND_UNKNOWN"
    ok = FrontendContribution(id="f", capability_id="c-tool", read_model="rm")
    assert ok.read_model == "rm"
    with pytest.raises(CapabilityContractError) as missing:
        FrontendContribution(id="f", capability_id="c-tool")
    assert missing.value.code == "CONTRIBUTION_READ_MODEL_MISSING"
