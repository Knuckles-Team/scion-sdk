"""Shared storage fixtures: an isolated filesystem store per test."""

import pytest

from scion_sdk.storage.filesystem import FilesystemBackend


@pytest.fixture
def fs_backend(tmp_path):
    """A filesystem backend rooted at a per-test temp directory."""
    return FilesystemBackend(root=str(tmp_path / "store"))
