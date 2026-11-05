"""SCION-CENTRALIZED-CAPABILITIES-R042.4: the filesystem backend confines keys to its bucket."""

import os

import pytest

from scion_sdk.storage.base import InvalidNameError, UnsupportedOperationError


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.4")
def test_filesystem_confines_keys_and_presign_unsupported(fs_backend, tmp_path):
    fs_backend.create_bucket("data")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, fs_backend.root / "data" / "link")
    with pytest.raises(InvalidNameError):
        fs_backend.put_object("data", "link/escape.txt", b"x")
    fs_backend.put_object("data", "ok.txt", b"x")
    with pytest.raises(UnsupportedOperationError) as excinfo:
        fs_backend.presigned_url("data", "ok.txt")
    assert excinfo.value.code == "STORAGE_UNSUPPORTED"
