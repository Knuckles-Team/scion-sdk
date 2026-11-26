"""Instance profile of a SPARQL endpoint.

SCION-CENTRALIZED-CAPABILITIES-R053. Three bounded ``SELECT`` queries produce
the profile the engine's ingest worker accepts (MEDULLA-INGEST-WORKER-R002.8).
The SDK builds the profile; the engine decides whether to admit it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scion_sdk.engine_capability import require_engine_capability
from scion_sdk.transports.sparql import SelectResult, SparqlClient

__all__ = [
    "InstanceProfileDraft",
    "InstanceProfileError",
    "build_instance_profile",
    "submit_instance_profile",
]

PROFILE_TRUNCATED = "PROFILE_TRUNCATED"
_DEFAULT_MAX_ENTRIES = 10_000


class InstanceProfileError(RuntimeError):
    """The profile was not produced; ``code`` is closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ProfileClassDraft(_Frozen):
    iri: str
    instances: int = Field(ge=0)


class ProfileDatatypePropertyDraft(_Frozen):
    iri: str
    class_: str = Field(alias="class")
    datatype: str
    presence: float


class ProfileObjectPropertyDraft(_Frozen):
    iri: str
    domain: str
    range_: str = Field(alias="range")
    usage: int = Field(ge=0)


class InstanceProfileDraft(_Frozen):
    """The three sections of the engine's ``InstanceProfile`` wire shape."""

    classes: tuple[ProfileClassDraft, ...] = ()
    datatype_properties: tuple[ProfileDatatypePropertyDraft, ...] = ()
    object_properties: tuple[ProfileObjectPropertyDraft, ...] = ()

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


_CLASSES = (
    "SELECT ?class (COUNT(?s) AS ?instances) WHERE {{ ?s a ?class }} "
    "GROUP BY ?class ORDER BY ?class LIMIT {limit}"
)
_DATATYPES = (
    "SELECT ?class ?property ?datatype (COUNT(DISTINCT ?s) AS ?carriers) WHERE "
    "{{ ?s a ?class ; ?property ?value . FILTER(isLiteral(?value)) "
    "BIND(DATATYPE(?value) AS ?datatype) }} "
    "GROUP BY ?class ?property ?datatype ORDER BY ?class ?property LIMIT {limit}"
)
_OBJECTS = (
    "SELECT ?domain ?property ?range (COUNT(*) AS ?usage) WHERE "
    "{{ ?s ?property ?o . ?s a ?domain . ?o a ?range }} "
    "GROUP BY ?domain ?property ?range ORDER BY ?domain ?property ?range "
    "LIMIT {limit}"
)


def _run(client: SparqlClient, template: str, limit: int) -> SelectResult:
    result = client.select(template.format(limit=limit))
    if len(result.rows) >= limit:
        raise InstanceProfileError(PROFILE_TRUNCATED)
    return result


def _value(row: dict[str, dict[str, str]], name: str) -> str:
    return row[name]["value"]


def build_instance_profile(
    client: SparqlClient, max_entries: int = _DEFAULT_MAX_ENTRIES
) -> InstanceProfileDraft:
    """Profile ``client``'s endpoint with three queries of ``max_entries`` rows.

    A query that fills its bound raises ``PROFILE_TRUNCATED`` and no profile is
    returned.
    """
    if max_entries < 1:
        raise ValueError("max_entries must be positive")
    classes = _run(client, _CLASSES, max_entries)
    datatypes = _run(client, _DATATYPES, max_entries)
    objects = _run(client, _OBJECTS, max_entries)
    instances = {_value(r, "class"): int(_value(r, "instances")) for r in classes.rows}
    return InstanceProfileDraft(
        classes=tuple(
            ProfileClassDraft(iri=iri, instances=count)
            for iri, count in instances.items()
        ),
        datatype_properties=tuple(
            ProfileDatatypePropertyDraft(
                iri=_value(r, "property"),
                **{"class": _value(r, "class")},
                datatype=_value(r, "datatype"),
                presence=min(
                    1.0,
                    int(_value(r, "carriers"))
                    / max(1, instances.get(_value(r, "class"), 0)),
                ),
            )
            for r in datatypes.rows
        ),
        object_properties=tuple(
            ProfileObjectPropertyDraft(
                iri=_value(r, "property"),
                domain=_value(r, "domain"),
                **{"range": _value(r, "range")},
                usage=int(_value(r, "usage")),
            )
            for r in objects.rows
        ),
    )


async def submit_instance_profile(
    profile: InstanceProfileDraft,
    *,
    proposal_id: str,
    send: Callable[[dict[str, Any]], Awaitable[object]],
) -> object:
    """Send ``profile`` in one ``proposals`` batch through the engine adapter.

    While the pinned engine lacks ``ProfileSubmission`` this raises
    ``EngineCapabilityUnavailable`` and ``send`` is never called.
    """
    require_engine_capability("ProfileSubmission")
    return await send(
        {"proposals": [{"proposal_id": proposal_id, "profile": profile.wire()}]}
    )
