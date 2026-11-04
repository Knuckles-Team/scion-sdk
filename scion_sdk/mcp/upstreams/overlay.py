"""The operator overlay: a TOML file that toggles, retunes or adds upstreams (R914.6).

The file named by ``SCION_UPSTREAMS_FILE`` holds ``[[upstream]]`` tables merged
by ``name``. An entry may change ``enabled``, ``activation``, ``kind`` and the
transport fields, and it may add an upstream. Switching the kind of an
existing upstream starts from a clean declaration, so stale transport fields
cannot survive. An entry that fails validation refuses the whole overlay with
``UPSTREAM_DECL_INVALID``; the connector declarations keep serving. The
``SCION_UPSTREAMS_DISABLED`` list wins over the overlay.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from scion_sdk.mcp.upstreams.model import (
    UPSTREAM_DECL_INVALID,
    UpstreamDecl,
    UpstreamDeclError,
    validate_declarations,
)

__all__ = ["merge_overlay"]


def _changes_kind(base: UpstreamDecl, entry: dict[str, object]) -> bool:
    kind = entry.get("kind")
    return isinstance(kind, str) and kind != base.kind


def merge_overlay(
    declarations: Sequence[UpstreamDecl],
    *,
    overlay_file: str | Path | None,
    disabled: Sequence[str],
) -> tuple[UpstreamDecl, ...]:
    """The declarations after the overlay and the disabled list are applied.

    Raises:
        UpstreamDeclError: ``UPSTREAM_DECL_INVALID`` when the overlay is
            unreadable or one of its entries fails validation. The caller then
            serves the connector declarations unchanged.
    """
    merged: list[UpstreamDecl] = list(declarations)
    if overlay_file:
        path = Path(overlay_file)
        if path.is_file():
            try:
                document = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                raise UpstreamDeclError(
                    UPSTREAM_DECL_INVALID, "overlay file is not readable TOML"
                ) from None
            entries = document.get("upstream", [])
            if not isinstance(entries, list):
                raise UpstreamDeclError(
                    UPSTREAM_DECL_INVALID, "overlay holds no [[upstream]] tables"
                )
            by_name = {decl.name: index for index, decl in enumerate(merged)}
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(
                    entry.get("name"), str
                ):
                    raise UpstreamDeclError(
                        UPSTREAM_DECL_INVALID, "overlay entry has no name"
                    )
                name = entry["name"]
                base = merged[by_name[name]] if name in by_name else None
                if base is None or _changes_kind(base, entry):
                    payload: dict[str, object] = {"name": name}
                    if base is not None and entry.get("namespace") is None:
                        payload["namespace"] = base.namespace
                    payload.update(
                        {key: value for key, value in entry.items() if key != "name"}
                    )
                else:
                    payload = base.model_dump()
                    payload.update(entry)
                try:
                    decl = UpstreamDecl.model_validate(payload)
                except (UpstreamDeclError, ValidationError) as exc:
                    raise UpstreamDeclError(
                        UPSTREAM_DECL_INVALID, f"overlay entry {name!r} is invalid"
                    ) from exc
                if base is not None:
                    merged[by_name[name]] = decl
                else:
                    by_name[name] = len(merged)
                    merged.append(decl)
    if disabled:
        disabled_names = set(disabled)
        merged = [
            decl.model_copy(update={"enabled": False})
            if decl.name in disabled_names
            else decl
            for decl in merged
        ]
    return validate_declarations(merged)
