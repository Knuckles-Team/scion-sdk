"""The credential access binding model (SCION-CONNECTOR-CONTROL-R035.1)."""

from __future__ import annotations

import pytest
from credential_support import make_binding
from pydantic import ValidationError

from scion_sdk.credentials.binding import (
    CredentialAccessBinding,
    binding_digest,
    refuse_request_authority,
)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R035.1")
def test_binding_round_trips_and_rejects_request_supplied_authority() -> None:
    binding = make_binding()
    again = CredentialAccessBinding.model_validate(binding.model_dump())
    assert again == binding and binding_digest(again) == binding_digest(binding)
    assert len(binding_digest(binding)) == 64
    assert binding_digest(make_binding(tenant="t2")) != binding_digest(binding)
    with pytest.raises(ValidationError):
        CredentialAccessBinding.model_validate({**binding.model_dump(), "secret": "x"})
    with pytest.raises(ValidationError):
        binding.tenant = "other"  # type: ignore[misc]
    with pytest.raises(ValueError, match="tenant"):
        refuse_request_authority({"tenant": "t9", "query": "x"})
    refuse_request_authority({"query": "x"})
