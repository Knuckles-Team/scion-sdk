"""Declarative upstream models and refusal taxonomy (SCION-CONNECTOR-CONTROL-R914).

The registry is data, not code. A connector describes upstream MCP servers as
:meth:`UpstreamDecl` values; the SDK validates, mounts and proxies them. Every
refusal in the registry speaks one code from :data:`REFUSAL_CODES`, so a
consumer can match on a stable token without parsing prose.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from scion_sdk.credentials.references import (
    SecretReferenceError,
    parse_secret_reference,
)

__all__ = [
    "REFUSAL_CODES",
    "BridgeDecl",
    "ExchangeBinding",
    "SandboxRoots",
    "UpstreamDecl",
    "UpstreamDeclError",
    "UpstreamError",
    "UpstreamStatus",
    "is_reserved_namespace",
    "validate_declarations",
]

# ---------------------------------------------------------------------------
# Refusal taxonomy. The registry's closed set; a consumer adds none of its own.
# ---------------------------------------------------------------------------

UPSTREAM_DECL_INVALID = "UPSTREAM_DECL_INVALID"
UPSTREAM_KIND_FIELDS = "UPSTREAM_KIND_FIELDS"
UPSTREAM_NAMESPACE_INVALID = "UPSTREAM_NAMESPACE_INVALID"
UPSTREAM_NAMESPACE_DUPLICATE = "UPSTREAM_NAMESPACE_DUPLICATE"
UPSTREAM_ROOTS_REQUIRED = "UPSTREAM_ROOTS_REQUIRED"
UPSTREAM_ROOT_UNSAFE = "UPSTREAM_ROOT_UNSAFE"
UPSTREAM_ROOT_OVERLAP = "UPSTREAM_ROOT_OVERLAP"
UPSTREAM_SECRET_LITERAL = "UPSTREAM_SECRET_LITERAL"  # nosec B105  # sanitizer:ignore - refusal code constant, not a secret value
UPSTREAM_EGRESS_REFUSED = "UPSTREAM_EGRESS_REFUSED"
UPSTREAM_BRIDGE_NOT_LOOPBACK = "UPSTREAM_BRIDGE_NOT_LOOPBACK"
UPSTREAM_BRIDGE_TOKEN_MISSING = "UPSTREAM_BRIDGE_TOKEN_MISSING"  # nosec B105 - refusal code constant, not a credential
UPSTREAM_TOOL_COLLISION = "UPSTREAM_TOOL_COLLISION"
UPSTREAM_STRATEGY_INVALID = "UPSTREAM_STRATEGY_INVALID"
UPSTREAM_NAMESPACE_UNKNOWN = "UPSTREAM_NAMESPACE_UNKNOWN"
UPSTREAM_DISABLED = "UPSTREAM_DISABLED"
UPSTREAM_ACTIVE_CAP = "UPSTREAM_ACTIVE_CAP"
UPSTREAM_CIRCUIT_OPEN = "UPSTREAM_CIRCUIT_OPEN"
UPSTREAM_POOL_FULL = "UPSTREAM_POOL_FULL"
UPSTREAM_SPAWN_FAILED = "UPSTREAM_SPAWN_FAILED"
UPSTREAM_DRIFT_REFUSED = "UPSTREAM_DRIFT_REFUSED"
UPSTREAM_ELICITATION_UNAVAILABLE = "UPSTREAM_ELICITATION_UNAVAILABLE"

#: Every refusal code the registry can emit, one taxonomy.
REFUSAL_CODES: frozenset[str] = frozenset(
    {
        UPSTREAM_DECL_INVALID,
        UPSTREAM_KIND_FIELDS,
        UPSTREAM_NAMESPACE_INVALID,
        UPSTREAM_NAMESPACE_DUPLICATE,
        UPSTREAM_ROOTS_REQUIRED,
        UPSTREAM_ROOT_UNSAFE,
        UPSTREAM_ROOT_OVERLAP,
        UPSTREAM_SECRET_LITERAL,
        UPSTREAM_EGRESS_REFUSED,
        UPSTREAM_BRIDGE_NOT_LOOPBACK,
        UPSTREAM_BRIDGE_TOKEN_MISSING,
        UPSTREAM_TOOL_COLLISION,
        UPSTREAM_STRATEGY_INVALID,
        UPSTREAM_NAMESPACE_UNKNOWN,
        UPSTREAM_DISABLED,
        UPSTREAM_ACTIVE_CAP,
        UPSTREAM_CIRCUIT_OPEN,
        UPSTREAM_POOL_FULL,
        UPSTREAM_SPAWN_FAILED,
        UPSTREAM_DRIFT_REFUSED,
        UPSTREAM_ELICITATION_UNAVAILABLE,
    }
)

# A namespace is a short lower-case token. The first underscore of a proxied
# name always splits namespace from tool, so a namespace may hold none.
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]{1,23}$")
# Names that would shadow the registry's own tooling.
_RESERVED_NAMESPACES = frozenset({"registry", "local"})


class UpstreamDeclError(ValueError):
    """A declaration or registry setting refuses at load time.

    ``code`` is one of :data:`REFUSAL_CODES`; the message names the offending
    upstream but never a secret value.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)


class UpstreamError(RuntimeError):
    """A runtime refusal raised against a session or the readiness probe."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)


def _refuse(code: str, detail: str = "") -> UpstreamDeclError:
    return UpstreamDeclError(code, detail)


def _check_reference(reference: str, what: str) -> None:
    """``what`` must hold a secret reference, never a literal value."""
    try:
        parse_secret_reference(reference)
    except SecretReferenceError:
        raise _refuse(
            UPSTREAM_SECRET_LITERAL, f"{what} must hold a secret reference"
        ) from None


class SandboxRoots(BaseModel):
    """A stdio child's filesystem grant, declared by the connector (R914.2).

    The connector supplies the two flag names so the SDK stays vendor-neutral.
    An empty ``read`` or ``write`` tuple is an explicit deny and passes no flag.
    A flag name of ``None`` says the upstream's CLI has no such flag: the SDK
    passes none, and roots on that side refuse rather than being dropped.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    read_flag: str | None
    write_flag: str | None

    @model_validator(mode="after")
    def _roots_need_flags(self) -> SandboxRoots:
        for side, roots, flag in (
            ("read", self.read, self.read_flag),
            ("write", self.write, self.write_flag),
        ):
            if roots and flag is None:
                raise _refuse(
                    UPSTREAM_DECL_INVALID,
                    f"{side} roots declared but the upstream has no {side} flag",
                )
        return self


class BridgeDecl(BaseModel):
    """A desktop bridge: a loopback address and a token, by reference (R914.4).

    The flag names default to the starting values and a connector may override
    them. ``token_ref`` is a secret reference; the value is materialized to a
    private file, never the command line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str
    token_ref: str
    address_flag: str = "--bridge"
    token_file_flag: str = "--control-token-file"


class ExchangeBinding(BaseModel):
    """Run each call of a local upstream through the artifact exchange (R914.13).

    ``path_args`` names the top-level arguments whose relative values the
    proxy rewrites into the run's output directory. Every argument that holds
    an ``exchange://`` reference or a URL is staged regardless.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path_args: tuple[str, ...] = ("path", "dir", "out", "out_dir")


UpstreamKind = Literal["stdio", "http", "bridge"]
ActivationMode = Literal["lazy", "active"]
PoolScope = Literal["server", "principal"]
OnDrift = Literal["warn", "refuse"]


class UpstreamDecl(BaseModel):
    """One declared upstream MCP server behind the connector (R914.1).

    ``namespace`` defaults to ``name``. ``kind`` selects which transport fields
    are legal; a field that belongs to another kind refuses. References hold
    secret *references* only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    namespace: str | None = None
    kind: UpstreamKind = "stdio"
    enabled: bool = True
    activation: ActivationMode | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}
    sandbox: SandboxRoots | None = None
    url: str | None = None
    credential: str | None = None
    tls_profile: str | None = None
    bridge: BridgeDecl | None = None
    pool_scope: PoolScope | None = None
    expected_fingerprint: str | None = None
    on_drift: OnDrift = "warn"
    exchange: ExchangeBinding | None = None

    @property
    def effective_namespace(self) -> str:
        """The namespace the proxied names use; defaults to ``name``."""
        return self.namespace if self.namespace is not None else self.name

    @property
    def effective_pool_scope(self) -> PoolScope:
        """``server`` for a local transport, ``principal`` for authenticated HTTP."""
        if self.pool_scope is not None:
            return self.pool_scope
        return "principal" if self.kind == "http" else "server"

    @model_validator(mode="after")
    def _validate(self) -> UpstreamDecl:
        namespace = self.effective_namespace
        if not _NAMESPACE_RE.fullmatch(namespace) or namespace in _RESERVED_NAMESPACES:
            raise _refuse(UPSTREAM_NAMESPACE_INVALID, f"namespace {namespace!r}")
        self._validate_kind_fields()
        self._validate_references()
        return self

    def _validate_kind_fields(self) -> None:
        kind = self.kind
        if kind in ("stdio", "bridge"):
            if self.command is None:
                raise _refuse(
                    UPSTREAM_KIND_FIELDS, f"{self.name}: {kind} requires command"
                )
            foreign = {"url", "credential", "tls_profile"}
            if kind == "stdio" and self.bridge is not None:
                foreign.add("bridge")
        elif kind == "http":
            if self.url is None:
                raise _refuse(UPSTREAM_KIND_FIELDS, f"{self.name}: http requires url")
            foreign = {"command", "args", "env", "sandbox", "bridge", "exchange"}
        else:  # pragma: no cover - Literal exhausts
            foreign = set()
        for field in foreign:
            value = getattr(self, field)
            if value not in (None, "", (), {}):
                raise _refuse(
                    UPSTREAM_KIND_FIELDS,
                    f"{self.name}: {field} is not a field of kind {kind}",
                )
        if kind == "bridge" and self.bridge is None:
            raise _refuse(UPSTREAM_KIND_FIELDS, f"{self.name}: bridge requires bridge")

    def _validate_references(self) -> None:
        for key, reference in self.env.items():
            _check_reference(reference, f"{self.name} env {key!r}")
        if self.credential is not None:
            _check_reference(self.credential, f"{self.name} credential")
        if self.bridge is not None:
            _check_reference(self.bridge.token_ref, f"{self.name} bridge token_ref")


UpstreamState = Literal[
    "disabled",
    "inactive",
    "starting",
    "ready",
    "degraded",
    "unavailable",
    "drifted",
    "circuit_open",
]


class UpstreamStatus(BaseModel):
    """The health of one upstream, as served by ``registry_status`` (R914.9).

    ``last_error_code`` holds a refusal *code*, never upstream error text.
    ``fingerprint`` and ``version`` come from the last successful
    ``tools/list``; ``inactive`` is a lazy upstream nobody activated, not a
    failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    namespace: str
    kind: UpstreamKind
    state: UpstreamState
    tool_count: int = 0
    fingerprint: str | None = None
    version: str | None = None
    last_error_code: str | None = None
    last_ok_at: float | None = None
    restarts: int = 0


def is_reserved_namespace(name: str) -> bool:
    """Whether ``name`` shadows the registry's own tooling."""
    return name in _RESERVED_NAMESPACES


def validate_declarations(
    declarations: Sequence[UpstreamDecl],
) -> tuple[UpstreamDecl, ...]:
    """A declaration set with unique namespaces and names (R914.1, R914.5).

    Raises:
        UpstreamDeclError: ``UPSTREAM_NAMESPACE_DUPLICATE`` for a repeated
            namespace, ``UPSTREAM_DECL_INVALID`` for a repeated name.
    """

    seen_namespaces: set[str] = set()
    seen_names: set[str] = set()
    for decl in declarations:
        namespace = decl.effective_namespace
        if namespace in seen_namespaces:
            raise _refuse(UPSTREAM_NAMESPACE_DUPLICATE, f"namespace {namespace!r}")
        if decl.name in seen_names:
            raise _refuse(
                UPSTREAM_DECL_INVALID, f"name {decl.name!r} is declared twice"
            )
        seen_namespaces.add(namespace)
        seen_names.add(decl.name)
    return tuple(declarations)
