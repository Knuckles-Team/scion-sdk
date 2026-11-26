"""Seed graph of a connector: instance identity, object catalog and references.

SCION-CONNECTOR-CONTROL-R068 and R907. The seed holds no bulk data. It is
emitted as sorted Turtle in three parts, each with a SHA-256 digest, and served
as ``seed://<connector>/seed.ttl``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "MAX_SEED_BYTES",
    "MAX_SEED_TRIPLES",
    "SEED_PARTS",
    "SeedGraphDeclaration",
    "SeedGraphError",
    "SeedTriple",
    "SeedTurtle",
    "emit_seed_turtle",
]

MAX_SEED_TRIPLES = 50_000
MAX_SEED_BYTES = 8 * 1024 * 1024
SEED_PARTS = ("identity", "catalog", "references")
_BULK_LITERAL_LIMIT = 1_024


class SeedGraphError(ValueError):
    """The seed is refused; ``code`` is closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SeedTriple:
    """One triple; ``literal`` marks ``object`` as a plain literal, else an IRI."""

    subject: str
    predicate: str
    object: str
    literal: bool = False

    def line(self) -> str:
        if self.literal:
            text = self.object.replace("\\", "\\\\").replace('"', '\\"')
            text = text.replace("\n", "\\n")
            obj = f'"{text}"'
        else:
            obj = f"<{self.object}>"
        return f"<{self.subject}> <{self.predicate}> {obj} ."


@dataclass(frozen=True)
class SeedGraphDeclaration:
    instance_iri: str
    identity: tuple[SeedTriple, ...] = ()
    catalog: tuple[SeedTriple, ...] = ()
    references: tuple[SeedTriple, ...] = ()

    def __post_init__(self) -> None:
        if not self.instance_iri.strip():
            raise SeedGraphError("seed_instance_iri_blank")


@dataclass(frozen=True)
class SeedTurtle:
    turtle: str
    parts: dict[str, str]
    digests: dict[str, str]
    digest: str


def _is_bulk(triple: SeedTriple) -> bool:
    if not triple.literal:
        return False
    text = triple.object.lstrip()
    return len(triple.object) > _BULK_LITERAL_LIMIT or text[:1] in ("[", "{")


def emit_seed_turtle(declaration: SeedGraphDeclaration) -> SeedTurtle:
    """Sorted Turtle in three parts with a digest each; deterministic."""
    parts: dict[str, str] = {}
    total = 0
    for name in SEED_PARTS:
        triples = getattr(declaration, name)
        if any(_is_bulk(t) for t in triples):
            raise SeedGraphError("seed_bulk_data")
        total += len(triples)
        parts[name] = "".join(
            f"{line}\n" for line in sorted({t.line() for t in triples})
        )
    turtle = "".join(parts[name] for name in SEED_PARTS)
    if total > MAX_SEED_TRIPLES or len(turtle.encode()) > MAX_SEED_BYTES:
        raise SeedGraphError("seed_too_large")
    digests = {
        name: hashlib.sha256(text.encode()).hexdigest() for name, text in parts.items()
    }
    return SeedTurtle(
        turtle, parts, digests, hashlib.sha256(turtle.encode()).hexdigest()
    )
