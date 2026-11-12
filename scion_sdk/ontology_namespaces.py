"""Ontology namespace IRIs: the one place the base IRI is written.

Every ontology module lives at ``<ONTOLOGY_BASE><module>`` and its terms at
``<ONTOLOGY_BASE><module>#<term>``. Code that needs a namespace derives it with
:func:`module_namespace`; nothing else hand-types an IRI under the base. The
Turtle files shipped in ``scion_sdk/ontology`` are plain data and are checked
against these constants by tests.
"""

from __future__ import annotations

ONTOLOGY_BASE = "https://knuckles-team.github.io/ontology/"


def module_iri(module: str) -> str:
    """Return the ontology IRI (no fragment separator) of a module."""
    return f"{ONTOLOGY_BASE}{module}"


def module_namespace(module: str) -> str:
    """Return the term namespace (ending in ``#``) of a module."""
    return f"{module_iri(module)}#"


CORE_NAMESPACE = module_namespace("core")
ACCESS_NAMESPACE = module_namespace("access")
SURFACE_CONTRACT_NAMESPACE = module_namespace("surface-contract")
