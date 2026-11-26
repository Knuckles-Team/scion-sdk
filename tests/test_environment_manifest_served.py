"""The SDK exports value-free environment declarations (SCION-CONNECTOR-CONTROL-R053)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

import scion_sdk
import scion_sdk.environment_census as census_module
from scion_sdk.env_scan import scan_setting_calls
from scion_sdk.environment import (
    COMPONENT,
    REFUSAL_CODES,
    SDK_READ_BINDINGS,
    STARTUP_REFUSAL_CODES,
    EnvironmentDeclaration,
    EnvironmentDeclarationError,
    EnvironmentDeclarationExport,
    EnvironmentDeclarationReceipt,
    EnvironmentReadBinding,
    environment_declarations,
    merge_declarations,
    startup_admission,
)
from scion_sdk.environment_census import EnvironmentRead, census

REVISION = scion_sdk.__version__
PACKAGE = Path(scion_sdk.__file__).resolve().parent
SECRET = "s3cr3t-value-that-must-never-appear"  # sanitizer:ignore - synthetic canary that must never appear in diagnostics


def _declaration(**changes: object) -> EnvironmentDeclaration:
    base: dict[str, object] = {
        "name": "SCION_PROBE",
        "component": "scion-sdk",
        "source_revision": REVISION,
        "type": "string",
        "required_when": "optional",
        "default": None,
        "sensitive": False,
        "call_sites": ("scion_sdk/x.py:1",),
    }
    return EnvironmentDeclaration(**{**base, **changes})  # type: ignore[arg-type]


def _code(call: object) -> str:
    with pytest.raises(EnvironmentDeclarationError) as caught:
        call()  # type: ignore[operator]
    return caught.value.code


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.1")
def test_model_rejects_duplicates_and_credential_defaults() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _declaration().name = "OTHER"  # type: ignore[misc]
    assert _code(lambda: _declaration(default="env://TOKEN")) == "unsafe_default"
    assert _code(lambda: _declaration(type="secret_reference", default="x")) == (
        "unsafe_default"
    )
    assert _code(lambda: _declaration(sensitive=True, default="x")) == (
        "sensitive_literal"
    )
    assert _code(lambda: _declaration(component="other")) == "unknown_component"
    assert _code(lambda: _declaration(type="float")) == "unsupported_constraint"
    unsorted = ("scion_sdk/b.py:1", "scion_sdk/a.py:2")
    assert _code(lambda: _declaration(call_sites=unsorted)) == "conflicting_declaration"
    assert _code(
        lambda: merge_declarations([_declaration(), _declaration(type="int")])
    ) == ("conflicting_declaration")
    merged = merge_declarations(
        [_declaration(), _declaration(call_sites=("scion_sdk/y.py:3",))]
    )
    assert [d.call_sites for d in merged] == [("scion_sdk/x.py:1", "scion_sdk/y.py:3")]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.2")
def test_missing_and_undeclared_inputs_block_export(tmp_path: Path) -> None:
    tree = tmp_path / "pkg"
    tree.mkdir()
    (tree / "reader.py").write_text(
        "import os\n\ndef read(name):\n    return os.environ.get(name)\n"
        "\ndef literal():\n    return os.environ['SCION_NOT_DECLARED']\n",
        encoding="utf-8",
    )
    code = _code(lambda: environment_declarations(REVISION, root=tree))
    assert code == "unresolved_dynamic_read"
    reads = census(tree)
    assert {(r.name, r.expression) for r in reads} == {
        (None, "name"),
        ("SCION_NOT_DECLARED", ""),
    }
    binding = EnvironmentReadBinding("pkg/reader.py", "name", "scion-sdk")
    declared = _declaration(name="SCION_NOT_DECLARED", call_sites=("pkg/reader.py:7",))
    export = environment_declarations(REVISION, [binding], root=tree, extra=[declared])
    assert "SCION_NOT_DECLARED" in {d.name for d in export.declarations}
    # The real package: every non-literal read is bound, nothing is wildcarded.
    assert environment_declarations(REVISION, SDK_READ_BINDINGS).receipt.outcome == (
        "exported"
    )
    assert (
        _code(lambda: environment_declarations(REVISION)) == "unresolved_dynamic_read"
    )
    exported = {
        d.name
        for d in environment_declarations(REVISION, SDK_READ_BINDINGS).declarations
    }
    assert scan_setting_calls(PACKAGE) <= exported


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.4")
def test_refusal_diagnostics_redact_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCION_BREAKER_FAILURES", SECRET)
    monkeypatch.setenv("ARD_SIGNING_PRIVATE_KEY", SECRET)
    attempts = {
        "source_revision_mismatch": lambda: environment_declarations(
            "not-" + REVISION, SDK_READ_BINDINGS
        ),
        "unknown_component": lambda: environment_declarations(
            REVISION, SDK_READ_BINDINGS, component="meristem"
        ),
        "conflicting_declaration": lambda: environment_declarations(
            REVISION,
            SDK_READ_BINDINGS,
            extra=[_declaration(name="TLS_PROFILE", type="int")],
        ),
        "unresolved_dynamic_read": lambda: environment_declarations(REVISION),
        "unsupported_constraint": lambda: _declaration(required_when="sometimes"),
        "unsafe_default": lambda: _declaration(default=f"vault://{SECRET}"),
        "sensitive_literal": lambda: _declaration(sensitive=True, default=SECRET),
        "unresolved_condition": lambda: environment_declarations(
            REVISION,
            SDK_READ_BINDINGS,
            extra=[_declaration(required_when="when:SCION_ABSENT")],
        ),
    }
    assert set(attempts) == set(REFUSAL_CODES)
    for expected, attempt in attempts.items():
        with pytest.raises(EnvironmentDeclarationError) as caught:
            attempt()
        error = caught.value
        assert error.code == expected
        assert error.receipt.outcome == "refused"
        assert error.receipt.declaration_digest is None
        assert error.receipt.declaration_count is None
        text = f"{error}{error.receipt!r}{error.subject}"
        assert SECRET not in text
        assert "env://" not in text
        assert "vault://" not in text


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.5")
def test_digests_reproduce_from_pinned_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = environment_declarations(REVISION, SDK_READ_BINDINGS, root=PACKAGE)
    second = environment_declarations(REVISION, SDK_READ_BINDINGS)
    assert first.receipt == second.receipt
    rows = [
        [d.name, d.type, d.required_when, d.default, d.sensitive, list(d.call_sites)]
        for d in first.declarations
    ]
    expected = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert first.receipt.declaration_digest == expected
    assert first.receipt.declaration_count == len(rows)
    assert first.receipt.schema_version == 1
    original = census_module.walk_files
    monkeypatch.setattr(
        census_module, "walk_files", lambda *a, **k: original(*a, **k)[::-1]
    )
    shuffled = environment_declarations(REVISION, SDK_READ_BINDINGS, root=PACKAGE)
    assert shuffled.receipt == first.receipt
    changed = environment_declarations(
        REVISION, SDK_READ_BINDINGS, extra=[_declaration(call_sites=("a/b.py:1",))]
    )
    assert changed.receipt.declaration_digest != first.receipt.declaration_digest
    assert changed.receipt.census_digest == first.receipt.census_digest


def _host_start(
    environment: dict[str, str], *, enabled: bool, extra: list[EnvironmentDeclaration]
) -> list[str]:
    """A host stand-in: composes the SDK fragment, admits, and only then opens."""
    opened: list[str] = []
    if not enabled:  # a disabled component needs no credentials and opens nothing
        return opened
    fragment = environment_declarations(REVISION, SDK_READ_BINDINGS, extra=extra)
    if startup_admission(fragment, environment, enabled=enabled):
        return opened
    opened.append("listener")
    return opened


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.3")
def test_host_startup_consumes_sdk_declaration_set() -> None:
    required = _declaration(name="SCION_PROBE", required_when="always")
    assert _host_start({}, enabled=True, extra=[required]) == []
    assert _host_start({}, enabled=False, extra=[required]) == []
    assert _host_start({"SCION_PROBE": "x"}, enabled=True, extra=[required]) == [
        "listener"
    ]
    export = environment_declarations(REVISION, SDK_READ_BINDINGS, extra=[required])
    assert startup_admission(export, {}) == ("SCION_PROBE: missing_required_input",)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053")
@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.6")
async def test_served_startup_accepts_declared_and_refuses_invalid() -> None:
    from fastmcp import Client, FastMCP

    export = environment_declarations(REVISION, SDK_READ_BINDINGS, root=PACKAGE)
    independent = {r.name for r in census(PACKAGE) if r.name}
    assert independent <= {d.name for d in export.declarations}
    valid = {"SCION_BREAKER_FAILURES": "3", "AIRGAP_MODE": "true"}
    invalid = {"SCION_BREAKER_FAILURES": SECRET}
    assert startup_admission(export, valid) == ()
    problems = startup_admission(export, invalid)
    assert problems == ("SCION_BREAKER_FAILURES: invalid_value",)
    assert SECRET not in "".join(problems)
    opened: list[str] = []
    for environment in (invalid, valid):
        if startup_admission(export, environment):
            continue
        server = FastMCP("probe")

        @server.tool
        def ping() -> str:
            return "pong"

        async with Client(server) as client:
            opened.append((await client.call_tool("ping", {})).data)
    assert opened == ["pong"]


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R053.2")
def test_export_types_and_startup_codes_are_the_declared_surface() -> None:
    export = environment_declarations(REVISION, SDK_READ_BINDINGS)
    assert isinstance(export, EnvironmentDeclarationExport)
    assert isinstance(export.receipt, EnvironmentDeclarationReceipt)
    assert {d.component for d in export.declarations} == {COMPONENT}
    assert all(isinstance(r, EnvironmentRead) for r in census(PACKAGE))
    problems = startup_admission(export, {"SCION_BREAKER_FAILURES": "x"})
    assert {p.split(": ")[1] for p in problems} <= set(STARTUP_REFUSAL_CODES)
