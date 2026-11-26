"""The typed observation producer contract (R910)."""

from __future__ import annotations

import pytest

from scion_sdk.observation import Observation, ObservationError, require_observation


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R910")
def test_observation_triple_required() -> None:
    seen = Observation(source="s", frame="f", revision=3, payload={"k": 1})
    assert require_observation(seen) is seen
    parsed = require_observation(
        {"source": "s", "frame": "f", "revision": 0, "payload": {}}
    )
    assert parsed.revision == 0
    for bad in (
        {"source": "s", "frame": "f"},
        "text",
        {"source": "", "frame": "f", "revision": 1, "payload": {}},
    ):
        with pytest.raises(ObservationError):
            require_observation(bad)
