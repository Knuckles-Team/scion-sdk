"""The per-package import scan SCION-CONNECTOR-CONTROL-R009-R013 verify by.

Each batch requirement's own verification column asks for the same proof:
"a per-package import scan ... confirming no meristem import
remains." This module is that scan, reusable across every alphabetical
batch (A-E, F-J, K-O, P-S, T-Z) rather than re-derived per batch, matching
the SCION-CONNECTOR-CONTROL-R004 precedent that contract-pin verification is
owned once by this SDK's own test kit, not recomputed downstream.

A connector package's own migration commit lands in that package's own
repository; this scan only proves the result, from source, by AST rather
than a brittle text search.
"""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess  # nosec B404
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from scion_sdk.testing._fleet_scan import _imports_meristem
from scion_sdk.testing._fleet_walk import (
    _DEFAULT_EXCLUDED_DIRS,
    PackageScanResult,
    _scan_python_sources,
)

__all__ = [
    "BATCH_LETTERS",
    "CensusReceipt",
    "JsonTagLoaderEntry",
    "LoaderInventoryItem",
    "OperationMapping",
    "PackageScanResult",
    "batch_census",
    "batch_roots",
    "fleet_census_receipt",
    "loader_parity_problems",
    "scan_for_meristem_imports",
    "scan_json_tag_loaders",
    "scan_many_packages",
]

#: SCION-CONNECTOR-CONTROL-R009-R013: the five alphabetical migration batches.
BATCH_LETTERS: dict[str, str] = {
    "R009": "abcde",
    "R010": "fghij",
    "R011": "klmno",
    "R012": "pqrs",
    "R013": "tuvwxyz",
}

_REGISTRATION_KINDS = frozenset({"operation", "callback", "action_dispatch"})


def scan_for_meristem_imports(
    package_root: Path,
    *,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDED_DIRS,
) -> PackageScanResult:
    """Scan Python imports without treating incomplete scans as migrated."""
    return _scan_python_sources(
        package_root, _imports_meristem, exclude_dirs=exclude_dirs
    )


def scan_many_packages(
    package_roots: Iterable[Path],
    *,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDED_DIRS,
) -> dict[str, PackageScanResult]:
    """Scan several package roots and return each one's result, keyed by name.

    SCION-CONNECTOR-CONTROL-R014 asks for "a cross-repository import scan
    showing no connector still depends on the superseded implementation".
    This is that scan's one call site: every batch requirement (R009-R013)
    and R014's own fleet-wide census share this function instead of each
    re-deriving a loop over :func:`scan_for_meristem_imports`.
    """
    results: dict[str, PackageScanResult] = {}
    for root in package_roots:
        if root.name in results:
            raise ValueError("connector package names must be unique")
        results[root.name] = scan_for_meristem_imports(root, exclude_dirs=exclude_dirs)
    return results


def batch_roots(
    agents_root: Path, first_letters: str, expected: Iterable[str] = ()
) -> list[Path]:
    """Return the connector roots of one batch, selected at call time.

    A batch is every directory under ``agents_root`` whose name starts with one
    of ``first_letters``. Each ``expected`` name that has no directory is still
    returned, so the census reports it as not migrated instead of omitting it.
    """
    letters = first_letters.lower()
    names = {
        p.name
        for p in agents_root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name[:1].lower() in letters
    }
    names.update(expected)
    return [agents_root / name for name in sorted(names)]


def batch_census(
    agents_root: Path,
    batch: str,
    expected: Iterable[str] = (),
    *,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDED_DIRS,
) -> dict[str, PackageScanResult]:
    """Scan one R009-R013 batch through the shared ``scan_many_packages`` call.

    ``batch`` is the row suffix (``"R009"`` to ``"R013"``). An unresolved
    dynamic import or a missing expected package is never reported clean.
    """
    roots = batch_roots(agents_root, BATCH_LETTERS[batch], expected)
    return scan_many_packages(roots, exclude_dirs=exclude_dirs)


@dataclass(frozen=True)
class OperationMapping:
    """One source-to-target operation binding with the facts that must match."""

    source_operation_id: str
    target_operation_id: str
    source_input_schema: str
    target_input_schema: str
    source_output_schema: str
    target_output_schema: str
    source_tags: frozenset[str] = frozenset()
    target_tags: frozenset[str] = frozenset()
    source_authorization: str = ""
    target_authorization: str = ""


@dataclass(frozen=True)
class JsonTagLoaderEntry:
    """SCION-CONNECTOR-CONTROL-R020.1: one JSON tag loader and its target mapping."""

    package: str
    source_revision: str
    loader_path: str
    json_path: str
    json_digest: str
    registration: str
    mappings: tuple[OperationMapping, ...] = ()


def loader_parity_problems(
    entries: Iterable[JsonTagLoaderEntry], inventory: Iterable[tuple[str, str]]
) -> list[str]:
    """Return every reason the loader inventory cannot gate a replacement.

    ``inventory`` lists the ``(package, loader_path)`` pairs found by a scan.
    An empty result means complete and schema, tag and authorization parity.
    """
    problems: list[str] = []
    entries = list(entries)
    seen = {(e.package, e.loader_path) for e in entries}
    for pair in sorted(set(inventory) - seen):
        problems.append(f"loader not inventoried: {pair[0]}:{pair[1]}")
    for entry in entries:
        problems.extend(_entry_problems(entry))
    return problems


def _entry_problems(entry: JsonTagLoaderEntry) -> list[str]:
    where = f"{entry.package}:{entry.loader_path}"
    problems: list[str] = []
    if entry.registration not in _REGISTRATION_KINDS:
        problems.append(f"{where}: unknown registration {entry.registration!r}")
    if not (entry.source_revision and entry.json_digest and entry.json_path):
        problems.append(f"{where}: incomplete entry")
    if not entry.mappings:
        problems.append(f"{where}: no operation mapping")
    for m in entry.mappings:
        problems.extend(
            _mapping_problems(
                f"{where}: {m.source_operation_id}->{m.target_operation_id}", m
            )
        )
    return problems


_MAPPING_FACETS = (
    ("source_input_schema", "target_input_schema", "input schema differs"),
    ("source_output_schema", "target_output_schema", "output schema differs"),
    ("source_tags", "target_tags", "tags differ"),
    ("source_authorization", "target_authorization", "authorization differs"),
)


def _mapping_problems(op: str, m: OperationMapping) -> list[str]:
    return [
        f"{op}: {message}"
        for source, target, message in _MAPPING_FACETS
        if getattr(m, source) != getattr(m, target)
    ]


@dataclass(frozen=True)
class CensusReceipt:
    """SCION-CONNECTOR-CONTROL-R014.27: one package's migration evidence."""

    package: str
    revision: str
    clean: bool
    scanned_files: int


def _git_head(root: Path) -> str:
    try:
        done = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    head = done.stdout.strip()
    return head if done.returncode == 0 and len(head) == 40 else ""


def _python_files(root: Path, exclude: frozenset[str]) -> list[Path]:
    found: list[Path] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in exclude]
        found.extend(Path(base) / name for name in files if name.endswith(".py"))
    return found


def fleet_census_receipt(
    roots: Iterable[Path],
    *,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDED_DIRS,
) -> tuple[CensusReceipt, ...]:
    """A receipt per connector root: package, git HEAD, clean, scanned files.

    A root that cannot be resolved to a package directory or to a git revision
    is unclean; the owner removes the superseded toolkit only after an
    all-clean receipt.
    """
    roots = list(roots)
    results = scan_many_packages(roots, exclude_dirs=exclude_dirs)
    receipts: list[CensusReceipt] = []
    for root in roots:
        result = results[root.name]
        resolved = root.is_dir()
        revision = _git_head(root) if resolved else ""
        receipts.append(
            CensusReceipt(
                package=result.package,
                revision=revision,
                clean=resolved and bool(revision) and result.migrated,
                scanned_files=len(_python_files(root, exclude_dirs)) if resolved else 0,
            )
        )
    return tuple(receipts)


@dataclass(frozen=True)
class LoaderInventoryItem:
    """SCION-CONNECTOR-CONTROL-R020.1: one JSON tag loader found in a package."""

    package: str
    loader_path: str
    json_path: str
    json_digest: str


def _json_literals(tree: ast.AST) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.endswith(".json")
    ]


def _reads_json(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr in {"load", "loads"}
        for node in ast.walk(tree)
    )


def scan_json_tag_loaders(
    package_roots: Iterable[Path],
    *,
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDED_DIRS,
) -> tuple[LoaderInventoryItem, ...]:
    """Find every loader path that reads a shipped JSON tag file, with its digest.

    A loader is a Python source that parses JSON and names a ``*.json`` file
    that exists under the package root. The digest is the file's SHA-256.
    """
    items: list[LoaderInventoryItem] = []
    for root in package_roots:
        for source in sorted(_python_files(root, exclude_dirs)):
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            if not _reads_json(tree):
                continue
            for literal in sorted(set(_json_literals(tree))):
                target = next(iter(root.rglob(Path(literal).name)), None)
                if target is None or not target.is_file():
                    continue
                items.append(
                    LoaderInventoryItem(
                        package=root.name,
                        loader_path=str(source.relative_to(root)),
                        json_path=str(target.relative_to(root)),
                        json_digest=hashlib.sha256(target.read_bytes()).hexdigest(),
                    )
                )
    return tuple(items)
