"""Strict JSON object decoding shared by governed write-back contracts."""

import json


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("invalid JSON constant")


def _decode_contract_json(data: bytes) -> object:
    """Refuse duplicate members, non-finite numbers and invalid UTF-8."""
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid contract JSON") from exc


def _canonical_bytes(value: object) -> bytes:
    """Encode one JSON value as the canonical contract bytes of this repository.

    The RFC 8785 profile: property names sorted by code point, compact
    separators, UTF-8, no non-finite numbers. Wallet contract profiles
    contain only ASCII property names, strings and exact nonnegative
    integers, so Python's compact sorted JSON matches JCS.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8", errors="strict")
