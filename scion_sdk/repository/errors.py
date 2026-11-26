"""Repository transport failures."""

__all__ = ["INDEX_REFUSAL_CODES", "IndexRefusal", "RepositoryTransportError"]

#: The closed set of delta index refusal codes (SCION-SOURCE-INGEST-R004.16).
INDEX_REFUSAL_CODES = frozenset(
    {
        "INDEX_LANGUAGE_UNSUPPORTED",
        "INDEX_TYPE_MODE_INVALID",
        "INDEX_SEQUENCE_STALE",
        "INDEX_SCHEMA_MISMATCH",
        "INDEX_RELEASE_PENDING",
        "INDEX_MEMORY_EXCEEDED",
    }
)


class RepositoryTransportError(RuntimeError):
    """A provider page or engine result violates the repository contract."""


class IndexRefusal(RepositoryTransportError):
    """A delta index request refused with one named code."""

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in INDEX_REFUSAL_CODES:
            raise ValueError(f"unknown index refusal code: {code}")
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)
