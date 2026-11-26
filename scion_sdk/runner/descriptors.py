"""Connector descriptors and runner settings.

A descriptor names a connector, how its MCP server is reached, which presets run, and which data
resources trigger which presets. Credentials are references only
(``env://NAME`` or ``openbao://mount/path#field``); a literal value is rejected.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from scion_sdk.auth.oidc import ClientCredentialsConfig
from scion_sdk.credentials.references import parse_secret_reference
from scion_sdk.runner.stages import StageDependency, StageLimits

__all__ = [
    "ConnectorDescriptor",
    "EndpointSpec",
    "RunnerConfig",
    "RunnerSettings",
    "StageDeclaration",
]

_CONNECTOR_NAME = r"^[a-z0-9][a-z0-9._-]{0,127}$"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EndpointSpec(_Strict):
    """Where a connector's MCP server is reached (streamable HTTP or stdio)."""

    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    bearer_token: str = ""
    client_credentials: ClientCredentialsConfig | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _check_endpoint(self) -> EndpointSpec:
        if bool(self.url) == bool(self.command):
            raise ValueError("an endpoint needs exactly one of url or command")
        if self.bearer_token and not self.url:
            raise ValueError("bearer_token applies only to a url endpoint")
        references = [*self.env.values(), self.bearer_token]
        for reference in filter(None, references):
            parse_secret_reference(reference)
        return self

    @model_validator(mode="after")
    def _check_client_credentials(self) -> EndpointSpec:
        if self.client_credentials is None:
            return self
        if not self.url:
            raise ValueError("client_credentials applies only to a url endpoint")
        if self.bearer_token:
            raise ValueError("set at most one of bearer_token and client_credentials")
        return self


class StageDeclaration(_Strict):
    """One concurrent stage: a preset's stream and the receipts it waits for."""

    stage_id: str = Field(min_length=1)
    preset: str = Field(min_length=1)
    stream: str = Field(min_length=1)
    dependencies: tuple[StageDependency, ...] = ()


class ConnectorDescriptor(_Strict):
    """One connector the runner serves.

    The server declares its sync presets at ``connector://resources``.
    ``presets`` empty means every declared sync preset.
    ``data_resources`` maps a resource URI to the presets synced when the
    server reports that resource updated.
    """

    connector: str = Field(pattern=_CONNECTOR_NAME)
    endpoint: EndpointSpec
    presets: tuple[str, ...] = ()
    provision: bool = True
    interval_seconds: float = Field(default=900.0, gt=0)
    data_resources: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    mapping_reference: str = ""
    empty_authoritative_approval: str = ""
    max_pages_per_cycle: int = Field(default=1_000, ge=1, le=1_000_000)
    #: May lower the governance interval, never below 300 (SCION-SOURCE-INGEST-R805.4).
    permission_sync_interval_s: int | None = Field(default=None, ge=300)
    tenant: str = ""
    stages: tuple[StageDeclaration, ...] = ()
    stage_limits: StageLimits | None = None

    @model_validator(mode="after")
    def _check_stages(self) -> ConnectorDescriptor:
        if self.stages and (self.stage_limits is None or not self.tenant):
            raise ValueError("STAGE_LIMITS_ABSENT: stages need stage_limits and tenant")
        unknown = {s.preset for s in self.stages} - set(
            self.presets or {s.preset for s in self.stages}
        )
        if unknown:
            raise ValueError("a stage names a preset that is not selected")
        return self

    @property
    def resolved_mapping_reference(self) -> str:
        """Configured exact mapping, or the single-mapping manifest shorthand."""
        return self.mapping_reference or f"connector:{self.connector}"


class RunnerSettings(_Strict):
    """Scheduler-wide limits."""

    max_concurrency: int = Field(default=4, ge=1, le=256)
    backoff_initial_seconds: float = Field(default=5.0, gt=0)
    backoff_max_seconds: float = Field(default=600.0, gt=0)
    registry_refresh_seconds: float = Field(default=60.0, gt=0)
    #: Operator lock of pinned release signer keys. Fixed safety: required, no
    #: default and no environment variable disables release verification.
    release_lock: Path

    @field_validator("release_lock")
    @classmethod
    def _anchor_lock(cls, value: Path, info: ValidationInfo) -> Path:
        base = (info.context or {}).get("base_dir")
        return base / value if base is not None and not value.is_absolute() else value


class RunnerConfig(_Strict):
    """The ``connector-sync`` configuration document."""

    settings: RunnerSettings
    connectors: tuple[ConnectorDescriptor, ...] = ()

    @model_validator(mode="after")
    def _unique_connectors(self) -> RunnerConfig:
        names = [descriptor.connector for descriptor in self.connectors]
        if len(names) != len(set(names)):
            raise ValueError("a connector is declared more than once")
        return self
