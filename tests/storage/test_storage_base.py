"""SCION-CENTRALIZED-CAPABILITIES-R042.1: one backend protocol, one error taxonomy."""

import pytest

from scion_sdk import storage
from scion_sdk.storage.base import (
    STORAGE_CODES,
    InvalidNameError,
    StorageError,
    validate_bucket_name,
    validate_key,
)


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.1")
def test_key_validation_refuses_traversal_and_errors_carry_codes():
    for bad in ["", "/abs", "a/../b", "a//b", "./a", "a\\b"]:
        with pytest.raises(InvalidNameError) as excinfo:
            validate_key(bad)
        assert excinfo.value.code == "STORAGE_NAME_INVALID"
    for bad in ["", "a/b", "..", ".hidden"]:
        with pytest.raises(InvalidNameError):
            validate_bucket_name(bad)
    assert validate_key("docs/a.txt") == "docs/a.txt"
    subclasses = [
        getattr(storage, name)
        for name in storage.__all__
        if isinstance(getattr(storage, name), type)
        and issubclass(getattr(storage, name), StorageError)
    ]
    codes = {cls.code for cls in subclasses}
    assert codes == STORAGE_CODES
    assert StorageError("x").code == "STORAGE_BACKEND_FAILED"
