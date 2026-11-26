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
import json
import os
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from scion_sdk.testing._fleet_scan import _imports_meristem
from scion_sdk.testing._fleet_walk import (
    _DEFAULT_EXCLUDED_DIRS,
    PackageScanResult,
    _scan_python_sources,
)
from scion_sdk.testing.major_version import (
    MajorVersionTransition,
    PackageCompatibilityReceipt,
    PackageInventoryEntry,
    PackageInventorySnapshot,
)

__all__ = [
    "BATCH_LETTERS",
    "CensusReceipt",
    "InventoryIncomplete",
    "JsonTagLoaderEntry",
    "LoaderClassification",
    "LoaderInventoryItem",
    "OperationMapping",
    "PackageScanResult",
    "ParityCall",
    "ParityProblem",
    "ParityReceipt",
    "ServerUnderParity",
    "SuiteResult",
    "batch_census",
    "batch_roots",
    "classify_json_tag_loaders",
    "evaluate_major_version_compatibility",
    "fleet_census_receipt",
    "loader_inventory_digest",
    "loader_parity_problems",
    "run_loader_parity",
    "scan_for_meristem_imports",
    "scan_json_tag_loaders",
    "scan_many_packages",
    "sorted_loader_entries",
]

#: SCION-CONNECTOR-CONTROL-R009-R013: the five alphabetical migration batches.
BATCH_LETTERS: dict[str, str] = {
    "R009": "abcde",
    "R010": "fghij",
    "R011": "klmno",
    "R012": "pqrs",
    "R013": "tuvwxyz",
}

_REGISTRATION_KINDS = frozenset({"operation", "explicit_callback", "action_dispatch"})


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
    registration_kind: str = "operation"
    refusal_cases: tuple[str, ...] = ()


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
    public_repository: str = ""
    python_target: str = ""


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
    problems.extend(_shape_problems(where, entry))
    for m in entry.mappings:
        problems.extend(
            _mapping_problems(
                f"{where}: {m.source_operation_id}->{m.target_operation_id}", m
            )
        )
    return problems


def _shape_problems(where: str, entry: JsonTagLoaderEntry) -> list[str]:
    """R054.1: repository-relative paths, unique ids and complete source/target pairs."""
    problems = [
        f"{where}: path is not repository-relative: {path}"
        for path in (entry.loader_path, entry.json_path)
        if path and (Path(path).is_absolute() or ".." in Path(path).parts)
    ]
    sources = [m.source_operation_id for m in entry.mappings]
    targets = [m.target_operation_id for m in entry.mappings]
    for label, ids in (("source", sources), ("target", targets)):
        problems.extend(
            f"{where}: duplicate {label} operation {name}"
            for name in sorted({n for n in ids if ids.count(n) > 1})
        )
    problems.extend(
        f"{where}: incomplete pair {m.source_operation_id!r}->{m.target_operation_id!r}"
        for m in entry.mappings
        if not (m.source_operation_id and m.target_operation_id)
    )
    problems.extend(
        f"{where}: unknown registration kind {m.registration_kind!r}"
        for m in entry.mappings
        if m.registration_kind not in _REGISTRATION_KINDS
    )
    return problems


def sorted_loader_entries(
    entries: Iterable[JsonTagLoaderEntry],
) -> tuple[JsonTagLoaderEntry, ...]:
    """Entries ordered by package, loader path and JSON path."""
    return tuple(sorted(entries, key=lambda e: (e.package, e.loader_path, e.json_path)))


def loader_inventory_digest(entries: Iterable[JsonTagLoaderEntry]) -> str:
    """SHA-256 of the canonical, sorted inventory; equal inventories digest equally."""
    payload = [asdict(e) for e in sorted_loader_entries(entries)]
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=sorted
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


class InventoryIncomplete(ValueError):
    """R007.2 ``INVENTORY_INCOMPLETE``: the inventory is not proven complete."""

    code = "INVENTORY_INCOMPLETE"


@dataclass(frozen=True)
class SuiteResult:
    """A package's own test-suite result against the target artifact."""

    suite: str
    run: str
    completed_at: datetime
    outcome: Literal["passed", "failed", "incomplete", "cancelled"]


#: Runs one package's own suite inside an isolated candidate directory.
SuiteRunner = Callable[
    [PackageInventoryEntry, MajorVersionTransition, Path], SuiteResult
]


def evaluate_major_version_compatibility(
    inventory: PackageInventorySnapshot,
    transition: MajorVersionTransition,
    *,
    declared_package_ids: Collection[str] | None,
    suite_runner: SuiteRunner,
) -> tuple[PackageCompatibilityReceipt, ...]:
    """One receipt per inventoried package, from that package's own suite result.

    Each package runs in its own temporary directory, removed afterwards; tracked
    manifests and lockfiles are never touched. ``declared_package_ids`` is the
    independent census of the fleet: ``None`` (unknown) or any difference from the
    inventory refuses with ``INVENTORY_INCOMPLETE``. An import scan is never
    evidence; only a suite result produces a receipt.
    """
    members = {entry.package_id for entry in inventory.entries}
    if declared_package_ids is None or set(declared_package_ids) != members:
        raise InventoryIncomplete("inventory completeness is not established")
    receipts: list[PackageCompatibilityReceipt] = []
    for entry in inventory.entries:
        with tempfile.TemporaryDirectory(prefix="major-version-") as candidate:
            result = suite_runner(entry, transition, Path(candidate))
        receipts.append(
            PackageCompatibilityReceipt(
                transition_id=transition.transition_id,
                inventory_digest=inventory.inventory_digest,
                package_id=entry.package_id,
                repository_id=entry.repository_id,
                source_commit=entry.source_commit,
                old_pin=transition.old_pin,
                target_pin=transition.target_pin,
                preimage_digest=entry.preimage_digest,
                target_artifact_digest=transition.target_artifact_digest,
                suite=result.suite,
                run=result.run,
                completed_at=result.completed_at,
                outcome=result.outcome,
            )
        )
    return tuple(receipts)


@dataclass(frozen=True)
class LoaderClassification:
    """R054.2: one JSON-reading registration path and whether it is migrated."""

    package: str
    loader_path: str
    json_path: str
    status: Literal["migrated", "unresolved"]
    reason: str


def _json_reading_sources(
    root: Path, exclude_dirs: frozenset[str]
) -> Iterable[tuple[Path, ast.AST | None]]:
    for source in sorted(_python_files(root, exclude_dirs)):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            yield source, None
            continue
        if _reads_json(tree):
            yield source, tree


def classify_json_tag_loaders(
    package_roots: Iterable[Path],
    entries: Iterable[JsonTagLoaderEntry],
    *,
    bindings: Collection[tuple[str, str]] = (),
    exclude_dirs: frozenset[str] = _DEFAULT_EXCLUDED_DIRS,
) -> tuple[LoaderClassification, ...]:
    """Classify every JSON-reading Python path as ``migrated`` or ``unresolved``.

    A path is migrated when an inventory entry covers it and that entry passes
    parity. A dynamic JSON path or an unreadable file is unresolved until an
    explicit ``(package, loader_path)`` binding explains it.
    """
    entries = list(entries)
    by_loader = {(e.package, e.loader_path): e for e in entries}
    explained = set(bindings)
    found: list[LoaderClassification] = []
    for root in package_roots:
        for source, tree in _json_reading_sources(root, exclude_dirs):
            rel = str(source.relative_to(root))
            key = (root.name, rel)
            literals = [] if tree is None else sorted(set(_json_literals(tree)))
            json_path = literals[0] if literals else ""
            if key in explained:
                status, reason = "migrated", "explained by binding"
            elif tree is None:
                status, reason = "unresolved", "unreadable source"
            elif not literals:
                status, reason = "unresolved", "dynamic JSON path"
            elif key not in by_loader:
                status, reason = "unresolved", "no inventory entry"
            else:
                problems = _entry_problems(by_loader[key])
                status = "unresolved" if problems else "migrated"
                reason = problems[0] if problems else "parity holds"
            found.append(
                LoaderClassification(root.name, rel, json_path, status, reason)  # type: ignore[arg-type]
            )
    return tuple(found)


#: R054.4 refusal codes of the served parity run.
PARITY_CODES = frozenset(
    {
        "unknown_loader",
        "unresolved_binding",
        "malformed_inventory",
        "operation_mismatch",
        "schema_difference",
        "missing_baseline",
        "backend_calls",
        "result_difference",
        "refusal_difference",
    }
)


@dataclass(frozen=True)
class ParityProblem:
    """One reason parity is not established, with a stable code."""

    code: str
    subject: str


@dataclass(frozen=True)
class ServerUnderParity:
    """A started server and its backend call counter."""

    server: Any
    backend_calls: Callable[[], int]


@dataclass(frozen=True)
class ParityCall:
    """One served ``tools/call`` driven against both servers."""

    source_operation_id: str
    arguments: Mapping[str, Any]
    permitted: bool


@dataclass(frozen=True)
class ParityReceipt:
    """Canonical, reference-only record of one parity run."""

    inventory_digest: str
    sdk_revision: str
    protocol_version: str
    fixture_seed: str
    comparisons: tuple[tuple[str, str, str], ...]
    backend_counts: tuple[tuple[str, int, int], ...]
    problems: tuple[ParityProblem, ...]
    unresolved: tuple[str, ...]

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _tool_tags(tool: Any) -> frozenset[str]:
    return frozenset(((tool.meta or {}).get("fastmcp") or {}).get("tags") or ())


def _outcome(result: Any) -> tuple[bool, Any]:
    if result.is_error:
        text = "".join(getattr(part, "text", "") for part in result.content)
        return True, text
    return False, result.structured_content


async def run_loader_parity(
    entries: Iterable[JsonTagLoaderEntry],
    source: ServerUnderParity | None,
    replacement: ServerUnderParity,
    calls: Iterable[ParityCall],
    *,
    sdk_revision: str,
    protocol_version: str,
    fixture_seed: str,
    inventory: Iterable[tuple[str, str]] = (),
    classifications: Iterable[LoaderClassification] = (),
) -> ParityReceipt:
    """Drive ``tools/list`` and ``tools/call`` on both servers and compare them.

    Missing old-server evidence blocks parity (``missing_baseline``); it never
    becomes an empty baseline. Permitted calls must reach each backend exactly
    once and refused calls zero times, with the same typed refusal on both.
    """
    from fastmcp import Client

    entries = sorted_loader_entries(entries)
    digest = loader_inventory_digest(entries)
    problems: list[ParityProblem] = []
    unresolved = tuple(
        f"{p[0]}:{p[1]}"
        for p in sorted(set(inventory) - {(e.package, e.loader_path) for e in entries})
    )
    problems.extend(ParityProblem("unknown_loader", item) for item in unresolved)
    problems.extend(
        ParityProblem("unresolved_binding", f"{c.package}:{c.loader_path}")
        for c in classifications
        if c.status == "unresolved"
    )
    for entry in entries:
        shape = _shape_problems(f"{entry.package}:{entry.loader_path}", entry)
        problems.extend(ParityProblem("malformed_inventory", text) for text in shape)
    if source is None:
        problems.append(ParityProblem("missing_baseline", "source server"))
        return ParityReceipt(
            digest,
            sdk_revision,
            protocol_version,
            fixture_seed,
            (),
            (),
            tuple(problems),
            unresolved,
        )
    mappings = {m.source_operation_id: m for e in entries for m in e.mappings}
    comparisons: list[tuple[str, str, str]] = []
    async with Client(source.server) as old, Client(replacement.server) as new:
        old_tools = {t.name: t for t in await old.list_tools()}
        new_tools = {t.name: t for t in await new.list_tools()}
        for source_id, mapping in sorted(mappings.items()):
            old_tool = old_tools.get(source_id)
            new_tool = new_tools.get(mapping.target_operation_id)
            if old_tool is None or new_tool is None:
                problems.append(ParityProblem("operation_mismatch", source_id))
                continue
            facets = {
                "input_schema": (old_tool.inputSchema, new_tool.inputSchema),
                "output_schema": (old_tool.outputSchema, new_tool.outputSchema),
                "tags": (
                    _tool_tags(old_tool) - {"granular"},
                    _tool_tags(new_tool) - {"granular"},
                ),
            }
            for facet, (before, after) in facets.items():
                same = before == after
                comparisons.append((source_id, facet, "equal" if same else "different"))
                if not same:
                    problems.append(
                        ParityProblem("schema_difference", f"{source_id}:{facet}")
                    )
        counts: list[tuple[str, int, int]] = []
        for call in calls:
            bound = mappings.get(call.source_operation_id)
            if bound is None:
                problems.append(
                    ParityProblem("unknown_loader", call.source_operation_id)
                )
                continue
            before_old, before_new = source.backend_calls(), replacement.backend_calls()
            old_result = await old.call_tool(
                call.source_operation_id, dict(call.arguments), raise_on_error=False
            )
            new_result = await new.call_tool(
                bound.target_operation_id, dict(call.arguments), raise_on_error=False
            )
            used_old = source.backend_calls() - before_old
            used_new = replacement.backend_calls() - before_new
            counts.append((call.source_operation_id, used_old, used_new))
            expected = 1 if call.permitted else 0
            if (used_old, used_new) != (expected, expected):
                problems.append(
                    ParityProblem("backend_calls", call.source_operation_id)
                )
            old_out, new_out = _outcome(old_result), _outcome(new_result)
            if old_out[0] != new_out[0] or old_out[0] == call.permitted:
                problems.append(
                    ParityProblem("refusal_difference", call.source_operation_id)
                )
            elif old_out != new_out:
                problems.append(
                    ParityProblem("result_difference", call.source_operation_id)
                )
            comparisons.append(
                (
                    call.source_operation_id,
                    "call",
                    "equal" if old_out == new_out else "different",
                )
            )
    return ParityReceipt(
        digest,
        sdk_revision,
        protocol_version,
        fixture_seed,
        tuple(comparisons),
        tuple(counts),
        tuple(problems),
        unresolved,
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m scion_sdk.testing.fleet_migration``: list every unresolved loader.

    ``--root`` is a connector package root; ``--entries`` is a JSON list of
    inventory entries. Exit 0 only when every JSON-reading path is migrated.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="fleet_migration")
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--entries", type=Path)
    args = parser.parse_args(argv)
    entries: list[JsonTagLoaderEntry] = []
    if args.entries:
        for raw in json.loads(args.entries.read_text(encoding="utf-8")):
            maps = tuple(
                OperationMapping(
                    **{
                        **m,
                        "source_tags": frozenset(m.get("source_tags", ())),
                        "target_tags": frozenset(m.get("target_tags", ())),
                        "refusal_cases": tuple(m.get("refusal_cases", ())),
                    }
                )
                for m in raw.pop("mappings", ())
            )
            entries.append(JsonTagLoaderEntry(**raw, mappings=maps))
    bad = [
        c
        for c in classify_json_tag_loaders(args.root, entries)
        if c.status == "unresolved"
    ]
    for c in bad:
        print(f"unresolved {c.package}:{c.loader_path}: {c.reason}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
