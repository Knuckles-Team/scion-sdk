"""Static conformance checks over a connector's source, metadata and ontology."""

from __future__ import annotations

import ast
import importlib
import tomllib
from pathlib import Path
from typing import Any

from scion_sdk.connector import ResolvedConnector
from scion_sdk.connector_identity import (
    CONNECTOR_ENTRY_POINT_GROUP,
    connector_name,
    distribution_version,
)

#: Entry-point groups the convention replaces; a connector declares none.
REMOVED_PROVIDER_GROUPS = (
    "scion_sdk.skill_providers",
    "scion_sdk.ontology_providers",
    "scion_sdk.source_connector_providers",
)


class ConformanceFailure(AssertionError):
    """A connector violates a conformance property."""


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ConformanceFailure(message)


def _python_files(resolved: ResolvedConnector) -> list[Path]:
    return sorted(resolved.dirs.root.glob("*.py"))


def trees(resolved: ResolvedConnector) -> list[tuple[Path, ast.Module]]:
    return [
        (p, ast.parse(p.read_text(encoding="utf-8"))) for p in _python_files(resolved)
    ]


def pyproject(resolved: ResolvedConnector) -> dict[str, Any]:
    path = resolved.dirs.root.parent / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _check_fleet_name(resolved: ResolvedConnector) -> None:
    """The fleet name is declared once and every literal in the package matches it."""
    _require(
        connector_name(resolved.package) == resolved.name,
        "identity differs from the declaration",
    )
    group = (
        pyproject(resolved)
        .get("project", {})
        .get("entry-points", {})
        .get(CONNECTOR_ENTRY_POINT_GROUP)
    )
    if group is not None:
        _require(list(group) == [resolved.name], f"declares {list(group)}")
    for path, tree in trees(resolved):
        for node in ast.walk(tree):
            _check_literal_connector(path, node, resolved.name)


def _check_literal_connector(path: Path, node: ast.AST, name: str) -> None:
    if not isinstance(node, ast.Call):
        return
    func = getattr(node.func, "id", getattr(node.func, "attr", ""))
    if func not in {"IngestBinding", "ConnectorContent"}:
        return
    for keyword in node.keywords:
        if keyword.arg == "connector" and isinstance(keyword.value, ast.Constant):
            _require(
                keyword.value.value == name,
                f"{path.name} spells connector {keyword.value.value!r}",
            )


def _check_version_from_metadata(resolved: ResolvedConnector) -> None:
    """The version comes from package metadata and no module spells its own."""
    _require(
        resolved.version == distribution_version(resolved.distribution),
        "version differs from metadata",
    )
    for path, tree in trees(resolved):
        for node in ast.walk(tree):
            if _spells_version(node):
                raise ConformanceFailure(
                    f"{path.name} spells __version__; read metadata"
                )
    module = importlib.import_module(resolved.package)
    declared = getattr(module, "__version__", None)
    _require(declared in (None, resolved.version), f"__version__ {declared!r} differs")


def _spells_version(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return False
    value = getattr(node, "value", None)
    return (
        any(getattr(t, "id", "") == "__version__" for t in targets)
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    )


def check_error_redaction(resolved: ResolvedConnector) -> None:
    """Reject connector-local redaction and response-body error leakage."""
    for path, tree in trees(resolved):
        for node in ast.walk(tree):
            finding = _error_redaction_finding(node)
            if finding is not None:
                raise ConformanceFailure(
                    f"{path.name}:{getattr(node, 'lineno', 0)}: {finding}"
                )


def _error_redaction_finding(node: ast.AST) -> str | None:
    if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) and node.name.startswith(("redact", "sanitize", "scrub", "mask_secret")):
        return "OWN_REDACTION_HELPER"
    if isinstance(node, ast.Raise) and _has_response_body(node.exc):
        return "RESPONSE_BODY_IN_ERROR"
    if _is_logging_call(node) and _has_response_body(node):
        return "RESPONSE_BODY_IN_ERROR"
    if isinstance(node, ast.Raise) and _raises_raw_status_error(node.exc):
        return "RAW_ERROR_TYPE"
    return None


def _is_logging_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
        "debug",
        "info",
        "warning",
        "error",
        "exception",
        "critical",
    }


def _has_response_body(node: ast.AST | None) -> bool:
    if node is None:
        return False
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Attribute) or candidate.attr not in {
            "text",
            "content",
            "body",
        }:
            continue
        value = candidate.value
        if isinstance(value, ast.Name) and value.id in {"response", "resp", "res"}:
            return True
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "response"
            and isinstance(value.value, ast.Name)
            and value.value.id == "exc"
        ):
            return True
    return False


def _raises_raw_status_error(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "HTTPStatusError":
        return False
    return isinstance(func.value, ast.Name) and func.value.id in {"requests", "httpx"}


def check_repository_hydration(resolved: ResolvedConnector) -> None:
    """Reject hydration outside the SDK; the one path is
    ``scion_sdk.repository.index_repository_snapshot``."""
    for path, tree in trees(resolved):
        for node in ast.walk(tree):
            finding = _hydration_finding(node)
            if finding is not None:
                raise ConformanceFailure(
                    f"{path.name}:{getattr(node, 'lineno', 0)}: {finding}"
                )


def _hydration_finding(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "index_repository"
    ):
        return "HYDRATION_OUTSIDE_SDK"
    if isinstance(node, ast.Name) and node.id == "IndexRepository":
        return "HYDRATION_OUTSIDE_SDK"
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.startswith("scion_sdk.repository._"):
            return "HYDRATION_PRIVATE_IMPORT"
        if module == "scion_sdk.repository":
            if any(alias.name == "IndexRepository" for alias in node.names):
                return "HYDRATION_OUTSIDE_SDK"
            if any(alias.name.startswith("_") for alias in node.names):
                return "HYDRATION_PRIVATE_IMPORT"
        return None
    if isinstance(node, ast.Import) and any(
        name.name.startswith("scion_sdk.repository._") for name in node.names
    ):
        return "HYDRATION_PRIVATE_IMPORT"
    return None
