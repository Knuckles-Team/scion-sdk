"""The host service and log provider interface (SCION-CENTRALIZED-CAPABILITIES-R056)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from scion_sdk.transports import (
    HOST_SERVICE_ADAPTERS,
    CommandHostServiceProvider,
    HostServiceError,
    HostServiceProvider,
)
from scion_sdk.transports.host_services import (
    HOST_SERVICE_OPERATIONS,
    HostServiceCategory,
    LogLines,
    ServiceList,
    ServiceStatus,
)

PLATFORMS = ("linux", "darwin", "windows")


def _provider(
    platform: str, out: str = "ok"
) -> tuple[CommandHostServiceProvider, list]:
    calls: list[Sequence[str]] = []

    def run(argv: Sequence[str]) -> str:
        calls.append(argv)
        return out

    return CommandHostServiceProvider(platform, run), calls


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R056")
def test_each_platform_maps_every_operation_and_refuses_the_unmapped() -> None:
    assert set(HOST_SERVICE_ADAPTERS) == set(HOST_SERVICE_OPERATIONS)
    for operation in HOST_SERVICE_OPERATIONS:
        assert set(HOST_SERVICE_ADAPTERS[operation]) == set(PLATFORMS)
    provider, calls = _provider("linux")
    assert isinstance(provider, HostServiceProvider)
    status = provider.restart_service("ssh")
    assert isinstance(status, ServiceStatus)
    assert isinstance(provider.list_services(), ServiceList)
    assert calls[0] == ["systemctl", "restart", "ssh"]
    plan9, none = _provider("plan9")
    with pytest.raises(HostServiceError) as refused:
        plan9.list_services()
    assert refused.value.category is HostServiceCategory.UNSUPPORTED_PLATFORM
    assert none == []


@pytest.mark.spec("SCION-CENTRALIZED-CAPABILITIES-R056")
def test_log_lines_are_redacted_and_option_like_names_refused() -> None:
    provider, _ = _provider("linux", "a\npassword=hunter2hunter2\nb")
    logs = provider.read_logs("ssh")
    assert isinstance(logs, LogLines)
    lines = logs.lines
    assert lines[0] == "a" and "hunter2" not in lines[1]
    with pytest.raises(HostServiceError) as refused:
        provider.service_status("--now")
    assert refused.value.category is HostServiceCategory.INVALID_ARGUMENT
