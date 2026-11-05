"""SCION-CENTRALIZED-CAPABILITIES-R042.3: the two hosted cloud backends map provider errors."""

from unittest.mock import MagicMock

import pytest

from scion_sdk.storage.azure_blob import AzureBlobBackend
from scion_sdk.storage.base import NotFoundError, StoreConfigError
from scion_sdk.storage.gcs import GCSBackend


class _NotFound(Exception):
    code = 404
    status_code = 404


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R042.3")
def test_gcs_and_azure_translate_errors_with_fake_clients():
    gcs_client = MagicMock()
    gcs_client.bucket.return_value.get_blob.side_effect = _NotFound()
    gcs = GCSBackend(client=gcs_client)
    with pytest.raises(NotFoundError) as excinfo:
        gcs.head_object("data", "ghost")
    assert excinfo.value.code == "STORAGE_NOT_FOUND"

    azure_client = MagicMock()
    azure_client.get_blob_client.return_value.get_blob_properties.side_effect = (
        _NotFound()
    )
    azure = AzureBlobBackend(client=azure_client)
    with pytest.raises(NotFoundError):
        azure.head_object("data", "ghost")
    with pytest.raises(StoreConfigError):
        AzureBlobBackend.__init__(
            object.__new__(AzureBlobBackend), connection_string=None
        )
