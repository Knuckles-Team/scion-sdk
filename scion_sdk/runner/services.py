"""The collaborators every connector worker shares."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scion_sdk.decide.runner import DecisionRunner
from scion_sdk.discovery import ActivationPolicy
from scion_sdk.ports.artifact_kind import ArtifactKind
from scion_sdk.ports.governance_source import GovernanceSource
from scion_sdk.ports.sink import Sink
from scion_sdk.ports.transport import Transport
from scion_sdk.runner.descriptors import RunnerSettings
from scion_sdk.runner.endpoints import EndpointFactory
from scion_sdk.runner.health_state import RunnerHealth

__all__ = ["RunnerServices"]


@dataclass(frozen=True)
class RunnerServices:
    """Ports and settings, composed once at the composition root."""

    transport: Transport
    sink: Sink
    kinds: tuple[ArtifactKind, ...]
    endpoints: EndpointFactory
    settings: RunnerSettings
    #: Pinned release signer public keys from the operator lock; verification
    #: is mandatory and fails closed (SCION-CONNECTOR-CONTROL-R650.1.4).
    release_keys: Mapping[str, bytes]
    #: Liveness/readiness state; ``None`` only in tests that build services
    #: directly without exercising the health surface.
    health: RunnerHealth | None = None
    #: The installed connector decision runner (Medulla-backed), when this process
    #: was given a tenant to decide as; ``None`` means every connector-side
    #: ``Decide`` call site stays its deterministic fallback, exactly as
    #: before this port existed.
    decide_runner: DecisionRunner | None = None
    source_adapter_policy: ActivationPolicy | None = None
    #: Per-connector source governance (access, groups, ids, containers).
    governance: Mapping[str, GovernanceSource] | None = None
