"""Column profile facts a SQL source sends with its column records.

SCION-SOURCE-INGEST-R905. The SDK sends facts only (type, foreign-key flag,
distinct ratio, sample size); embedding admission is the engine's decision.
"""

from __future__ import annotations

from collections.abc import Sequence

from medulla.generated._shared import SqlColumnProfile, SqlColumnType

from scion_sdk.config import setting

__all__ = [
    "PROFILE_INCOMPLETE",
    "ProfileRefusal",
    "build_column_profile",
    "sample_rows",
]

PROFILE_INCOMPLETE = "PROFILE_INCOMPLETE"
PROFILE_SAMPLE_UNAVAILABLE = "PROFILE_SAMPLE_UNAVAILABLE"
_Q32 = 1 << 32
_NUMERIC = ("int", "serial", "num", "dec", "real", "float", "double", "money")


class ProfileRefusal(ValueError):
    """The profile is not produced; ``code`` is closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def sample_rows() -> int:
    """``SQL_PROFILE_SAMPLE_ROWS``: default 1000, bounds 100 to 100000."""
    value = int(setting("SQL_PROFILE_SAMPLE_ROWS", 1000))
    return min(max(value, 100), 100_000)


def _column_type(data_type: str) -> SqlColumnType:
    lowered = data_type.lower()
    if "bool" in lowered or lowered == "bit":
        return SqlColumnType.BOOLEAN
    if "time" in lowered or "date" in lowered:
        return SqlColumnType.TIMESTAMP
    if any(token in lowered for token in _NUMERIC):
        return SqlColumnType.NUMERIC
    return SqlColumnType.TEXT


def build_column_profile(
    *, data_type: str | None, is_foreign_key: bool, values: Sequence[object]
) -> SqlColumnProfile:
    """Profile one column from its declared type and a bounded non-null sample."""
    if not data_type:
        raise ProfileRefusal(PROFILE_INCOMPLETE)
    sample = [value for value in values if value is not None]
    distinct = len({repr(value) for value in sample})
    return SqlColumnProfile(
        sql_type=_column_type(data_type),
        is_foreign_key=is_foreign_key,
        distinct_ratio_q32=distinct * _Q32 // len(sample) if sample else 0,
        sample_count=len(sample),
    )
