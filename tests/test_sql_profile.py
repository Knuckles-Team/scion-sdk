"""SQL column profile facts from a sqlite fixture (SCION-SOURCE-INGEST-R905)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from medulla.generated._shared import SqlColumnProfile, SqlColumnType

from scion_sdk.ingest.sql_profile import (
    PROFILE_INCOMPLETE,
    ProfileRefusal,
    build_column_profile,
    sample_rows,
)
from scion_sdk.source_connectors.universal import UniversalConnector


def _database(tmp_path: Path) -> UniversalConnector:
    path = tmp_path / "fixture.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE owners (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE items (
            id INTEGER PRIMARY KEY, owner_id INTEGER REFERENCES owners(id),
            active BOOLEAN, note TEXT, kind TEXT, created TIMESTAMP);
        INSERT INTO owners VALUES (1, 'a'), (2, 'b');
        INSERT INTO items VALUES
          (1, 1, 1, 'free text one', 'red', '2026-01-01'),
          (2, 1, 0, 'free text two', 'red', '2026-01-02'),
          (3, 2, 1, 'free text three', 'blue', '2026-01-03'),
          (4, 2, 0, 'free text four', 'blue', '2026-01-04');
        """
    )
    db.commit()
    db.close()
    return UniversalConnector(str(path), kind="sqlite")


@pytest.mark.spec("SCION-SOURCE-INGEST-R905")
def test_profile_round_trips_and_every_field_is_filled(tmp_path: Path) -> None:
    profiles = _database(tmp_path).column_profiles()
    owner = profiles[("items", "owner_id")]
    assert isinstance(owner, SqlColumnProfile) and owner.is_foreign_key is True
    assert profiles[("items", "active")].sql_type is SqlColumnType.BOOLEAN
    assert profiles[("items", "created")].sql_type is SqlColumnType.TIMESTAMP
    note, kind = profiles[("items", "note")], profiles[("items", "kind")]
    assert note.sql_type is SqlColumnType.TEXT and note.distinct_ratio_q32 == 1 << 32
    assert kind.distinct_ratio_q32 == (2 << 32) // 4 and kind.sample_count == 4
    assert SqlColumnProfile.model_validate(note.model_dump()) == note


@pytest.mark.spec("SCION-SOURCE-INGEST-R905")
def test_incomplete_column_and_bounded_sample_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ProfileRefusal) as caught:
        build_column_profile(data_type="", is_foreign_key=False, values=[1])
    assert caught.value.code == PROFILE_INCOMPLETE
    monkeypatch.setenv("SQL_PROFILE_SAMPLE_ROWS", "5")
    assert sample_rows() == 100
    monkeypatch.setenv("SQL_PROFILE_SAMPLE_ROWS", "999999")
    assert sample_rows() == 100_000
