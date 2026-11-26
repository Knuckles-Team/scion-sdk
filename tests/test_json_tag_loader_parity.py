"""SCION-CONNECTOR-CONTROL-R020.1: JSON tag-loader inventory and parity gate."""

from __future__ import annotations

import dataclasses

import pytest

from scion_sdk.testing.fleet_migration import (
    JsonTagLoaderEntry,
    OperationMapping,
    loader_parity_problems,
)


def _mapping(**kw: object) -> OperationMapping:
    base = dict(
        source_operation_id="a",
        target_operation_id="a",
        source_input_schema="i",
        target_input_schema="i",
        source_output_schema="o",
        target_output_schema="o",
        source_tags=frozenset({"t"}),
        target_tags=frozenset({"t"}),
        source_authorization="read",
        target_authorization="read",
    )
    base.update(kw)
    return OperationMapping(**base)  # type: ignore[arg-type]


def _entry(*mappings: OperationMapping, registration: str = "operation"):
    return JsonTagLoaderEntry(
        "pkg",
        "abc",
        "pkg/load.py",
        "pkg/tags.json",
        "sha256:1",
        registration,
        mappings,
    )


def test_complete_matching_inventory_has_no_problems() -> None:
    assert loader_parity_problems([_entry(_mapping())], [("pkg", "pkg/load.py")]) == []


def test_uninventoried_loader_is_reported() -> None:
    problems = loader_parity_problems([_entry(_mapping())], [("pkg", "pkg/other.py")])
    assert any("not inventoried" in p for p in problems)


@pytest.mark.parametrize(
    ("change", "text"),
    [
        ({"target_input_schema": "x"}, "input schema"),
        ({"target_output_schema": "x"}, "output schema"),
        ({"target_tags": frozenset()}, "tags differ"),
        ({"target_authorization": "write"}, "authorization"),
    ],
)
def test_parity_rejects_differences(change: dict, text: str) -> None:
    problems = loader_parity_problems(
        [_entry(_mapping(**change))], [("pkg", "pkg/load.py")]
    )
    assert any(text in p for p in problems)


def test_unknown_registration_and_empty_mappings_are_rejected() -> None:
    bad = dataclasses.replace(_entry(), registration="magic")
    problems = loader_parity_problems([bad], [])
    assert any("unknown registration" in p for p in problems)
    assert any("no operation mapping" in p for p in problems)


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054.1")
def test_entries_validate_order_and_digest() -> None:
    from scion_sdk.testing.fleet_migration import (
        loader_inventory_digest,
        sorted_loader_entries,
    )

    b = dataclasses.replace(_entry(_mapping()), package="b")
    a = _entry(_mapping(), registration="action_dispatch")
    assert [e.package for e in sorted_loader_entries([b, a])] == ["pkg", "b"][::-1] or [
        e.package for e in sorted_loader_entries([b, a])
    ] == ["b", "pkg"]
    assert loader_inventory_digest([a, b]) == loader_inventory_digest([b, a])
    assert loader_inventory_digest([a]) != loader_inventory_digest([b])
    dup = _entry(_mapping(), _mapping())
    problems = loader_parity_problems([dup], [])
    assert any("duplicate source operation a" in p for p in problems)
    assert any("duplicate target operation a" in p for p in problems)
    absolute = dataclasses.replace(_entry(_mapping()), loader_path="/abs/load.py")
    assert any(
        "repository-relative" in p for p in loader_parity_problems([absolute], [])
    )
    incomplete = _entry(_mapping(target_operation_id=""))
    assert any("incomplete pair" in p for p in loader_parity_problems([incomplete], []))
    bad_kind = _entry(_mapping(registration_kind="magic"))
    assert any("registration kind" in p for p in loader_parity_problems([bad_kind], []))


@pytest.mark.spec("SCION-CONNECTOR-CONTROL-R054.2")
def test_scan_flags_hidden_and_unmapped_loaders(tmp_path) -> None:
    from scion_sdk.testing.fleet_migration import (
        classify_json_tag_loaders,
        main,
        scan_json_tag_loaders,
    )

    root = tmp_path / "pkg"
    root.mkdir()
    (root / "tags.json").write_text("{}")
    (root / "mapped.py").write_text('import json\nD = json.load(open("tags.json"))\n')
    (root / "hidden.py").write_text(
        "import json, os\nD = json.load(open(os.environ['P']))\n"
    )
    (root / "broken.py").write_text("import json\ndef (:\n")
    (root / "unmapped.py").write_text(
        'import json\nD = json.loads(open("tags.json").read())\n'
    )
    entry = dataclasses.replace(
        _entry(_mapping()), loader_path="mapped.py", json_path="tags.json"
    )
    result = {c.loader_path: c for c in classify_json_tag_loaders([root], [entry])}
    assert result["mapped.py"].status == "migrated"
    assert result["hidden.py"].reason == "dynamic JSON path"
    assert result["broken.py"].reason == "unreadable source"
    assert result["unmapped.py"].reason == "no inventory entry"
    explained = classify_json_tag_loaders(
        [root], [entry], bindings={("pkg", "hidden.py"), ("pkg", "broken.py")}
    )
    assert {c.loader_path: c.status for c in explained}["hidden.py"] == "migrated"
    assert {c.loader_path: c.status for c in explained}["broken.py"] == "migrated"
    assert len(scan_json_tag_loaders([root])) == 2
    assert main(["--root", str(root)]) == 1
