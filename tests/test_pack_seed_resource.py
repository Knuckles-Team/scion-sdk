"""A served seed resource is captured into the pack and bounded (R907)."""

from __future__ import annotations

import pytest

from scion_sdk.artifacts.resources import ResourceArtifactKind
from scion_sdk.contracts import CapturedArtifact, ServerIdentity
from scion_sdk.manifest.seed_graph import (
    MAX_SEED_TRIPLES,
    SeedGraphDeclaration,
    SeedTriple,
    emit_seed_turtle,
)
from scion_sdk.ports.errors import MalformedArtifactError

NS = "https://example.test/"
SERVER = ServerIdentity(name="demo", version="1.0.0")


class _Resource:
    def __init__(self, uri: str) -> None:
        self.uri = uri
        self.name = uri
        self.mimeType = "text/turtle"
        self.annotations = None


class _Session:
    def __init__(self, bodies: dict[str, str]) -> None:
        self.bodies = bodies

    async def list_resources(self) -> list[_Resource]:
        return [_Resource(uri) for uri in self.bodies]

    async def read_resource(self, uri: str) -> str:
        return self.bodies[uri]


def _seed(count: int) -> str:
    return emit_seed_turtle(
        SeedGraphDeclaration(
            NS + "i",
            catalog=tuple(
                SeedTriple(NS + "i", NS + "has", f"{NS}o/{n}") for n in range(count)
            ),
        )
    ).turtle


def _entry(body: str) -> CapturedArtifact:
    return CapturedArtifact(
        kind="resource",
        uri="seed://demo/seed.ttl",
        name="seed",
        media_type="text/turtle",
        body=body,
        server=SERVER,
    )


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R907")
async def test_seed_resource_captured_into_pack() -> None:
    kind = ResourceArtifactKind()
    body = _seed(3)
    (captured,) = await kind.list_entries(
        _Session({"seed://demo/seed.ttl": body}),
        SERVER,  # type: ignore[arg-type]
    )
    assert captured.uri == "seed://demo/seed.ttl" and captured.body == body
    kind.validate(captured)
    assert await kind.list_entries(_Session({}), SERVER) == ()  # type: ignore[arg-type]
    oversize = "".join(
        f"<{NS}i> <{NS}has> <{NS}o/{n}> .\n" for n in range(MAX_SEED_TRIPLES + 1)
    )
    with pytest.raises(MalformedArtifactError, match="SEED_TOO_LARGE"):
        kind.validate(_entry(oversize))
