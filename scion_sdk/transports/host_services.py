"""Host service and log provider interface (SCION-CENTRALIZED-CAPABILITIES-R056).

One typed provider reaches service and log operations on every supported
operating system. ``HOST_SERVICE_ADAPTERS`` names the native tooling per
operation and system; a system with no adapter for an operation refuses with
``UNSUPPORTED_PLATFORM`` and never falls back to another system's tool. Log
lines pass through the SDK redaction before they leave the provider.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from scion_sdk.httpsupport.redaction import redact_text

__all__ = [
    "HOST_SERVICE_ADAPTERS",
    "HOST_SERVICE_OPERATIONS",
    "CommandHostServiceProvider",
    "HostServiceCategory",
    "HostServiceError",
    "HostServiceProvider",
    "LogLines",
    "ServiceList",
    "ServiceStatus",
]

HOST_SERVICE_OPERATIONS: tuple[str, ...] = (
    "list_services",
    "service_status",
    "start_service",
    "stop_service",
    "restart_service",
    "read_logs",
)

#: Operation -> operating system -> argv template (``{service}``, ``{since}``,
#: ``{limit}`` are substituted as whole argv items; no shell is involved).
HOST_SERVICE_ADAPTERS: Mapping[str, Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "list_services": MappingProxyType(
            {
                "linux": ("systemctl", "list-units", "--type=service", "--no-pager"),
                "darwin": ("launchctl", "list"),
                "windows": ("powershell", "-Command", "Get-Service"),
            }
        ),
        "service_status": MappingProxyType(
            {
                "linux": ("systemctl", "status", "--no-pager", "{service}"),
                "darwin": ("launchctl", "print", "{service}"),
                "windows": ("powershell", "-Command", "Get-Service", "{service}"),
            }
        ),
        "start_service": MappingProxyType(
            {
                "linux": ("systemctl", "start", "{service}"),
                "darwin": ("launchctl", "kickstart", "{service}"),
                "windows": ("powershell", "-Command", "Start-Service", "{service}"),
            }
        ),
        "stop_service": MappingProxyType(
            {
                "linux": ("systemctl", "stop", "{service}"),
                "darwin": ("launchctl", "kill", "SIGTERM", "{service}"),
                "windows": ("powershell", "-Command", "Stop-Service", "{service}"),
            }
        ),
        "restart_service": MappingProxyType(
            {
                "linux": ("systemctl", "restart", "{service}"),
                "darwin": ("launchctl", "kickstart", "-k", "{service}"),
                "windows": ("powershell", "-Command", "Restart-Service", "{service}"),
            }
        ),
        "read_logs": MappingProxyType(
            {
                "linux": (
                    "journalctl",
                    "-u",
                    "{service}",
                    "--since",
                    "{since}",
                    "-n",
                    "{limit}",
                    "--no-pager",
                ),
                "darwin": (
                    "log",
                    "show",
                    "--predicate",
                    'process == "{service}"',
                    "--start",
                    "{since}",
                ),
                "windows": (
                    "powershell",
                    "-Command",
                    "Get-WinEvent",
                    "-ProviderName",
                    "{service}",
                    "-MaxEvents",
                    "{limit}",
                ),
            }
        ),
    }
)


class HostServiceCategory(StrEnum):
    """The closed failure categories of a host service operation."""

    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    COMMAND_FAILED = "COMMAND_FAILED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"


class HostServiceError(Exception):
    """A host service operation failed; ``category`` is closed, no tool output."""

    def __init__(self, category: HostServiceCategory) -> None:
        super().__init__(category.value)
        self.category = category


@dataclass(frozen=True)
class ServiceList:
    """The raw service listing of the host."""

    platform: str
    output: str


@dataclass(frozen=True)
class ServiceStatus:
    """The status output of one named service."""

    platform: str
    service: str
    output: str


@dataclass(frozen=True)
class LogLines:
    """Redacted log lines of one service, oldest first."""

    platform: str
    service: str
    lines: tuple[str, ...]


@runtime_checkable
class HostServiceProvider(Protocol):
    """Service and log operations on one host, the same on every system."""

    def list_services(self) -> ServiceList: ...
    def service_status(self, service: str) -> ServiceStatus: ...
    def start_service(self, service: str) -> ServiceStatus: ...
    def stop_service(self, service: str) -> ServiceStatus: ...
    def restart_service(self, service: str) -> ServiceStatus: ...
    def read_logs(
        self, service: str, since: str = "1 hour ago", limit: int = 200
    ) -> LogLines: ...


#: Runs one argv and returns its stdout; raises ``OSError`` or ``RuntimeError`` on failure.
CommandRunner = Callable[[Sequence[str]], str]


class CommandHostServiceProvider:
    """A provider that builds each command from ``HOST_SERVICE_ADAPTERS``."""

    def __init__(self, platform: str, runner: CommandRunner) -> None:
        self._platform = platform
        self._runner = runner

    def _run(self, operation: str, **values: str) -> str:
        template = HOST_SERVICE_ADAPTERS[operation].get(self._platform)
        if template is None:
            raise HostServiceError(HostServiceCategory.UNSUPPORTED_PLATFORM)
        for value in values.values():
            if not value or value.startswith("-") or "\x00" in value:
                raise HostServiceError(HostServiceCategory.INVALID_ARGUMENT)
        argv = [part.format(**values) for part in template]
        try:
            return self._runner(argv)
        except (OSError, RuntimeError):
            raise HostServiceError(HostServiceCategory.COMMAND_FAILED) from None

    def list_services(self) -> ServiceList:
        return ServiceList(self._platform, self._run("list_services"))

    def service_status(self, service: str) -> ServiceStatus:
        out = self._run("service_status", service=service)
        return ServiceStatus(self._platform, service, out)

    def start_service(self, service: str) -> ServiceStatus:
        out = self._run("start_service", service=service)
        return ServiceStatus(self._platform, service, out)

    def stop_service(self, service: str) -> ServiceStatus:
        out = self._run("stop_service", service=service)
        return ServiceStatus(self._platform, service, out)

    def restart_service(self, service: str) -> ServiceStatus:
        out = self._run("restart_service", service=service)
        return ServiceStatus(self._platform, service, out)

    def read_logs(
        self, service: str, since: str = "1 hour ago", limit: int = 200
    ) -> LogLines:
        if limit < 1:
            raise HostServiceError(HostServiceCategory.INVALID_ARGUMENT)
        out = self._run("read_logs", service=service, since=since, limit=str(limit))
        lines = tuple(redact_text(line) for line in out.splitlines())
        return LogLines(self._platform, service, lines)
