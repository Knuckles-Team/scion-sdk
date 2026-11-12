"""The shipped Turtle vocabularies use the one ontology base."""

from __future__ import annotations

import re
from pathlib import Path

import scion_sdk
from scion_sdk.ontology_namespaces import (
    ACCESS_NAMESPACE,
    CORE_NAMESPACE,
    ONTOLOGY_BASE,
    SURFACE_CONTRACT_NAMESPACE,
    module_iri,
    module_namespace,
)

ONTOLOGY_DIR = Path(scion_sdk.__file__).parent / "ontology"


def test_module_helpers_derive_from_the_base() -> None:
    assert module_iri("core") == ONTOLOGY_BASE + "core"
    assert module_namespace("surface-contract") == SURFACE_CONTRACT_NAMESPACE
    assert CORE_NAMESPACE.endswith("/core#")
    assert ACCESS_NAMESPACE.endswith("/access#")


def test_shipped_vocabularies_use_only_the_base() -> None:
    iri = re.compile(r"<(https?://[^>\s]+)>")
    allowed = ("http://www.w3.org/", "https://www.w3.org/", ONTOLOGY_BASE)
    for path in sorted(ONTOLOGY_DIR.glob("*.ttl")):
        for found in iri.findall(path.read_text(encoding="utf-8")):
            assert found.startswith(allowed), (path.name, found)
