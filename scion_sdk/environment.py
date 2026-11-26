"""Environment declarations of the SDK.

SCION-CONNECTOR-CONTROL-R908 and R053. ``environment_declarations`` exports a
value-free declaration for every environment name the SDK and its transports read,
from a static census of the source (:mod:`scion_sdk.environment_census`). It reads no
live value and does no network or subprocess work. A read whose name is not a literal
needs an ``EnvironmentReadBinding``; an unbound read makes the export unavailable.
``startup_admission`` is the SDK half of host startup: it checks a proposed environment
against the declarations before anything is opened.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from scion_sdk.environment_census import EnvironmentRead, census
from scion_sdk.version import __version__

__all__ = [
    "COMPONENT",
    "REFUSAL_CODES",
    "SCHEMA_VERSION",
    "SDK_READ_BINDINGS",
    "STARTUP_REFUSAL_CODES",
    "EnvironmentDeclaration",
    "EnvironmentDeclarationError",
    "EnvironmentDeclarationExport",
    "EnvironmentDeclarationReceipt",
    "EnvironmentReadBinding",
    "declarations",
    "environment_declarations",
    "merge_declarations",
    "startup_admission",
]

SCHEMA_VERSION = 1
COMPONENT = "scion-sdk"
TYPES = frozenset({"string", "int", "bool", "secret_reference", "enum"})
REFUSAL_CODES = (
    "source_revision_mismatch",
    "unknown_component",
    "conflicting_declaration",
    "unresolved_dynamic_read",
    "unsupported_constraint",
    "unsafe_default",
    "sensitive_literal",
    "unresolved_condition",
)
STARTUP_REFUSAL_CODES = ("missing_required_input", "invalid_value")
_SECRET_SCHEME = re.compile(r"^(env|vault|openbao|file|secret)://", re.IGNORECASE)
_CONDITION = re.compile(r"^when:([A-Z][A-Z0-9_]*)$")


class EnvironmentDeclarationError(RuntimeError):
    """The export is unavailable. Carries a receipt and a code, never a value."""

    def __init__(
        self, code: str, receipt: EnvironmentDeclarationReceipt, subject: str
    ) -> None:
        super().__init__(f"{receipt.component}: {subject}: {code}")
        self.code = code
        self.receipt = receipt
        self.subject = subject


@dataclass(frozen=True)
class EnvironmentDeclarationReceipt:
    """Evidence of one export attempt; the host records its own admission."""

    outcome: str  # "exported" or "refused"
    component: str
    source_revision: str
    schema_version: int
    declaration_count: int | None
    declaration_digest: str | None
    census_digest: str | None
    unresolved_call_sites: tuple[str, ...]
    reason: str | None = None


def _refused(
    code: str, component: str, revision: str, unresolved: Iterable[str] = ()
) -> EnvironmentDeclarationReceipt:
    return EnvironmentDeclarationReceipt(
        "refused", component, revision, SCHEMA_VERSION, None, None, None,
        tuple(sorted(unresolved)), code,
    )  # fmt: skip


@dataclass(frozen=True)
class EnvironmentDeclaration:
    """One environment name the SDK reads (the ARBORA-HOST-R010.2 row shape)."""

    name: str
    component: str
    source_revision: str
    type: str
    required_when: str
    default: str | None
    sensitive: bool
    call_sites: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        def refuse(code: str) -> EnvironmentDeclarationError:
            receipt = _refused(code, self.component, self.source_revision)
            return EnvironmentDeclarationError(code, receipt, self.name)

        if self.component != COMPONENT:
            raise refuse("unknown_component")
        if self.type not in TYPES:
            raise refuse("unsupported_constraint")
        if self.required_when not in {"always", "optional"} and not _CONDITION.match(
            self.required_when
        ):
            raise refuse("unsupported_constraint")
        if self.default is not None:
            if self.sensitive:
                raise refuse("sensitive_literal")
            if self.type == "secret_reference" or _SECRET_SCHEME.match(self.default):
                raise refuse("unsafe_default")
        if list(self.call_sites) != sorted(self.call_sites, key=_site_key):
            raise refuse("conflicting_declaration")


def _site_key(site: str) -> tuple[str, int]:
    path, _, line = site.rpartition(":")
    return path, int(line)


@dataclass(frozen=True)
class EnvironmentReadBinding:
    """Names the inputs a non-literal read serves.

    ``expression`` is the source text of the name argument, so a binding survives a
    line shift. ``inputs`` are exact names, or ``{PREFIX}_NAME`` templates when the
    connector supplies the prefix; an empty tuple means a pass-through reader whose
    callers' literal names are censused on their own.
    """

    path: str
    expression: str
    owner: str
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnvironmentDeclarationExport:
    declarations: tuple[EnvironmentDeclaration, ...]
    receipt: EnvironmentDeclarationReceipt


# (name, type, call sites); generated from the literal-read scan, kept sorted.
_TABLE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("AGENT_CONNECTOR_STATE_DIR", "str", ("scion_sdk.writeback_sinks._env",)),
    ("AGENT_PROVIDER_PROFILE", "str", ("scion_sdk.auth.provider_profile",)),
    ("AGENT_USER_MAP", "str", ("scion_sdk.writeback_sinks.spec_link",)),
    ("AIRGAP_MODE", "bool", ("scion_sdk.http.client",)),
    ("ANSIBLE_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ansible",)),
    ("ARCHIMATE_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.archimate",)),
    ("ARD_SIGNING_PRIVATE_KEY", "str", ("scion_sdk.signing",)),
    ("AUDIENCE", "str", ("scion_sdk.auth.delegation",)),
    ("AUTH_TYPE", "str", ("scion_sdk.mcp.parser",)),
    ("CADDY_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ops",)),
    (
        "CISO_ASSISTANT_ENABLE_WRITE",
        "bool",
        ("scion_sdk.writeback_sinks.sinks.ciso_assistant",),
    ),
    ("CONNECTOR_AUTH_CLIENT_ID", "str", ("scion_sdk.config",)),
    ("CONNECTOR_AUTH_TOKEN_URL", "str", ("scion_sdk.config",)),
    ("CONNECTOR_CONFIG_FILE", "str", ("scion_sdk.config",)),
    ("CONNECTOR_JWT_AUDIENCE", "str", ("scion_sdk.mcp.served_security",)),
    ("CONNECTOR_LIMITS_MAX_CONNECTIONS", "int", ("scion_sdk.config",)),
    ("CONNECTOR_LIMITS_MAX_KEEPALIVE_CONNECTIONS", "int", ("scion_sdk.config",)),
    ("CONNECTOR_PATH_ORDER_TIMEOUT_MS", "int", ("scion_sdk.adapters.platform_order",)),
    ("CONNECTOR_PRINCIPAL_GRANTS", "str", ("scion_sdk.identity.transport",)),
    ("CONNECTOR_REQUIRED_SCOPES", "str", ("scion_sdk.mcp.served_security",)),
    ("CONNECTOR_TRANSPORT_ALLOW_PLAINTEXT", "bool", ("scion_sdk.config",)),
    ("CONNECTOR_TRANSPORT_TIMEOUT_SECONDS", "int", ("scion_sdk.config",)),
    ("DELEGATED_SCOPES", "str", ("scion_sdk.auth.delegation",)),
    ("EGERIA_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.egeria",)),
    ("EMBEDDING_ALLOWED_PRIVATE_HOSTS", "bool", ("scion_sdk.embeddings",)),
    ("EMBEDDING_ALLOW_PLAINTEXT", "bool", ("scion_sdk.embeddings",)),
    ("EMBEDDING_API_KEY_REF", "str", ("scion_sdk.embeddings",)),
    ("EMBEDDING_BASE_URL", "str", ("scion_sdk.embeddings",)),
    ("EMBEDDING_MODEL", "str", ("scion_sdk.embeddings",)),
    ("EMBEDDING_PROVIDER", "str", ("scion_sdk.embeddings",)),
    ("EMERALD_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.emerald",)),
    ("ENABLE_DELEGATION", "bool", ("scion_sdk.auth.delegation",)),
    ("ERPNEXT_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.erpnext",)),
    ("FASTMCP_SERVER_AUTH_JWT_AUDIENCE", "str", ("scion_sdk.mcp.served_security",)),
    ("FASTMCP_SERVER_AUTH_JWT_ISSUER", "str", ("scion_sdk.mcp.parser",)),
    ("GITHUB_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.issue_tracker",)),
    ("GITLAB_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.issue_tracker",)),
    ("HARNESS_MANIFEST_PATH", "str", ("scion_sdk.manifest.harness",)),
    (
        "HOMEASSISTANT_ENABLE_WRITE",
        "bool",
        ("scion_sdk.writeback_sinks.sinks.home_assistant",),
    ),
    ("HOST", "str", ("scion_sdk.mcp.parser",)),
    ("JIRA_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.issue_tracker",)),
    ("KAFKA_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ops",)),
    ("KEYCLOAK_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.identity",)),
    ("KG_EA_WRITEBACK", "str", ("scion_sdk.writeback_sinks.sinks.capability",)),
    ("KG_PROCESS_WRITEBACK", "str", ("scion_sdk.writeback_sinks.sinks.process",)),
    ("LEANIX_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.leanix",)),
    ("LEGAL_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.legal",)),
    ("LGTM_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ops",)),
    ("MCP_CONFIG", "str", ("scion_sdk.source_connectors.connectors._mcp_stdio",)),
    ("MCP_DISABLED_TAGS", "str", ("scion_sdk.mcp.visibility_policy",)),
    ("MCP_DISABLED_TOOLS", "str", ("scion_sdk.mcp.visibility_policy",)),
    ("MCP_ENABLED_TAGS", "str", ("scion_sdk.mcp.visibility_policy",)),
    ("MCP_ENABLED_TOOLS", "str", ("scion_sdk.mcp.visibility_policy",)),
    ("MCP_FLEET_REGISTRATION", "str", ("scion_sdk.mcp.server",)),
    ("MCP_FLEET_REGISTRATION_TTL_SECS", "int", ("scion_sdk.mcp.registry",)),
    ("MCP_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS", "int", ("scion_sdk.mcp.parser",)),
    ("MCP_KEEPALIVE_TIMEOUT_SECONDS", "int", ("scion_sdk.mcp.parser",)),
    ("MCP_LISTEN_BACKLOG", "int", ("scion_sdk.mcp.parser",)),
    ("MCP_MAX_CONNECTIONS", "int", ("scion_sdk.mcp.parser",)),
    ("MCP_MAX_REQUEST_BYTES", "int", ("scion_sdk.mcp.parser",)),
    ("MCP_REQUEST_BODY_TIMEOUT_SECONDS", "int", ("scion_sdk.mcp.parser",)),
    (
        "MCP_STDIO_PROHIBITED",
        "bool",
        ("scion_sdk.source_connectors.connectors._mcp_stdio",),
    ),
    ("MCP_TLS_TERMINATED", "bool", ("scion_sdk.mcp.parser",)),
    ("MEALIE_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.mealie",)),
    ("MEDULLA_AUDIENCE", "str", ("scion_sdk.ingest.engine",)),
    ("MEDULLA_AUTH_SECRET_REF", "str", ("scion_sdk.ingest.engine",)),
    ("MEDULLA_ENDPOINT", "str", ("scion_sdk.ingest.engine",)),
    ("MEDULLA_GRAPH", "str", ("scion_sdk.ingest.engine",)),
    ("MEDULLA_POLICY_VERSION", "str", ("scion_sdk.ingest.engine",)),
    ("MEDULLA_PRINCIPAL", "str", ("scion_sdk.ingest.engine",)),
    ("MEDULLA_TENANT", "str", ("scion_sdk.ingest.engine",)),
    ("NEXTCLOUD_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.nextcloud",)),
    ("NO_PROXY", "str", ("scion_sdk.tls.selection",)),
    ("OIDC_AUDIENCE", "str", ("scion_sdk.auth.oidc",)),
    ("OIDC_CLIENT_ID", "str", ("scion_sdk.auth.delegation", "scion_sdk.auth.oidc")),
    (
        "OIDC_CLIENT_SECRET_REF",
        "str",
        ("scion_sdk.auth.delegation", "scion_sdk.auth.oidc"),
    ),
    ("OIDC_ISSUER", "str", ("scion_sdk.auth.oidc", "scion_sdk.mcp.parser")),
    ("OIDC_SCOPE", "str", ("scion_sdk.auth.oidc",)),
    ("OIDC_TOKEN_URL", "str", ("scion_sdk.auth.delegation", "scion_sdk.auth.oidc")),
    ("OKTA_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.identity",)),
    ("OPENBAO_ADDR", "str", ("scion_sdk.credentials.openbao",)),
    ("OPENBAO_NAMESPACE", "str", ("scion_sdk.credentials.openbao",)),
    ("OPENBAO_TOKEN_REF", "str", ("scion_sdk.credentials.openbao",)),
    ("PERSISTENCE_IDENTITY_HMAC_KEY_REF", "str", ("scion_sdk.privacy_reference",)),
    ("PERSISTENCE_PRIVACY_DENY_TERMS_REF", "str", ("scion_sdk.privacy_rules",)),
    ("PLANE_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.issue_tracker",)),
    ("PORT", "int", ("scion_sdk.mcp.parser",)),
    ("PORTAINER_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ops",)),
    ("PROVIDER_CONFIGS", "str", ("scion_sdk.auth.provider_profile",)),
    ("REQUESTS_CA_BUNDLE", "str", ("scion_sdk.tls.selection",)),
    ("RUNNER_DECIDE_TENANT", "str", ("scion_sdk.runner.cli",)),
    ("RUNNER_HEALTH_ADDR", "str", ("scion_sdk.runner.cli",)),
    (
        "SALESFORCE_ENABLE_WRITE",
        "bool",
        ("scion_sdk.writeback_sinks.sinks.salesforce",),
    ),
    ("SCION_ALLOW_DESTRUCTIVE", "bool", ("scion_sdk.mcp.destructive",)),
    ("SCION_BREAKER_COOLDOWN_S", "int", ("scion_sdk.resilience",)),
    ("SCION_BREAKER_FAILURES", "int", ("scion_sdk.resilience",)),
    ("SCION_EXCHANGE_FILE", "str", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_LINEAGE", "str", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_MAX_INPUT_BYTES", "int", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_MAX_OUTPUTS", "int", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_MAX_OUTPUT_BYTES", "int", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_RETENTION_S", "int", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_STAGING_ROOT", "str", ("scion_sdk.exchange.config",)),
    ("SCION_EXCHANGE_URL_INPUTS", "str", ("scion_sdk.exchange.config",)),
    ("SCION_FIXTURE_MODE", "bool", ("scion_sdk.mcp.parser",)),
    (
        "SCION_HEALTH_DEGRADED_FAILURES",
        "int",
        ("scion_sdk.connector_lifecycle", "scion_sdk.runner.health_state"),
    ),
    ("SCION_TOOL_DEADLINE_SECONDS", "int", ("scion_sdk.mcp.call_context",)),
    ("SCION_UPSTREAMS_CALL_TIMEOUT_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_CIRCUIT_COOLDOWN_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_CONNECT_TIMEOUT_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_FILE", "str", ("scion_sdk.connector._serve",)),
    ("SCION_UPSTREAMS_IDLE_TTL_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_INHERIT_ENV", "bool", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_LIST_CACHE_TTL_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    (
        "SCION_UPSTREAMS_MAX_ACTIVE_NAMESPACES",
        "int",
        ("scion_sdk.mcp.upstreams.loader",),
    ),
    ("SCION_UPSTREAMS_MAX_PROCESSES", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_PROBE_INTERVAL_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_RESTART_LIMIT", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_RESTART_WINDOW_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCION_UPSTREAMS_STOP_GRACE_S", "int", ("scion_sdk.mcp.upstreams.loader",)),
    ("SCM_WEBHOOK_SECRET_REF", "str", ("scion_sdk.scm.events",)),
    (
        "SERVICENOW_ENABLE_WRITE",
        "bool",
        ("scion_sdk.writeback_sinks.sinks.servicenow",),
    ),
    ("SOURCE_CREDENTIALS", "str", ("scion_sdk.auth.source_credentials",)),
    ("SOURCE_HTTP_MAX_REDIRECTS", "int", ("scion_sdk.source_connectors.http_safety",)),
    (
        "SOURCE_HTTP_MAX_RESPONSE_BYTES",
        "int",
        ("scion_sdk.source_connectors.http_safety",),
    ),
    ("SOURCE_MOUNT_BACKEND", "str", ("scion_sdk.source.mount",)),
    ("SOURCE_PERMISSION_SYNC_INTERVAL_S", "int", ("scion_sdk.runner.governance",)),
    ("SQL_PROFILE_SAMPLE_ROWS", "int", ("scion_sdk.ingest.sql_profile",)),
    ("SSL_CERT_DIR", "str", ("scion_sdk.tls.selection",)),
    ("SSL_CERT_FILE", "str", ("scion_sdk.tls.selection",)),
    ("TECHNITIUM_DNS_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ops",)),
    ("TLS_CA_BUNDLE", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CA_BUNDLE_REF", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CA_DIRECTORY", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CLIENT_CERT", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CLIENT_CERT_REF", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CLIENT_KEY", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CLIENT_KEY_PASSWORD_REF", "str", ("scion_sdk.tls.selection",)),
    ("TLS_CLIENT_KEY_REF", "str", ("scion_sdk.tls.selection",)),
    ("TLS_MINIMUM_VERSION", "str", ("scion_sdk.tls.selection",)),
    (
        "TLS_PROFILE",
        "str",
        (
            "scion_sdk.config.instances",
            "scion_sdk.embeddings",
            "scion_sdk.mcp.env_drift",
            "scion_sdk.tls.resolve",
            "scion_sdk.tls.selection",
        ),
    ),
    ("TLS_PROFILES", "str", ("scion_sdk.tls.resolve", "scion_sdk.tls.selection")),
    ("TLS_PROFILES_REF", "str", ("scion_sdk.tls.resolve", "scion_sdk.tls.selection")),
    (
        "TLS_PROFILE_REF",
        "str",
        (
            "scion_sdk.config.instances",
            "scion_sdk.embeddings",
            "scion_sdk.mcp.env_drift",
            "scion_sdk.tls.selection",
        ),
    ),
    ("TLS_PROXY_URL", "str", ("scion_sdk.tls.selection",)),
    ("TLS_PROXY_URL_REF", "str", ("scion_sdk.tls.selection",)),
    ("TLS_SYSTEM_TRUST", "bool", ("scion_sdk.tls.selection",)),
    ("TLS_TRUST_ENV", "bool", ("scion_sdk.tls.selection",)),
    ("TRANSPORT", "int", ("scion_sdk.mcp.parser",)),
    ("TWENTY_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.twenty",)),
    ("UPTIME_KUMA_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.ops",)),
    ("WGER_ENABLE_WRITE", "bool", ("scion_sdk.writeback_sinks.sinks.wger",)),
    ("XDG_CONFIG_HOME", "str", ("scion_sdk.config", "scion_sdk.paths._platform")),
    ("XDG_RUNTIME_DIR", "str", ("scion_sdk.tls.material",)),
    (
        "XDG_STATE_HOME",
        "str",
        (
            "scion_sdk.library_state",
            "scion_sdk.runner.cli",
            "scion_sdk.writeback_sinks._env",
        ),
    ),
)

_SENSITIVE = frozenset({"ARD_SIGNING_PRIVATE_KEY"})
_PACKAGE_ROOT = Path(__file__).resolve().parent
_KIND = {"str": "string", "int": "int", "bool": "bool"}


def _table_type(name: str, kind: str) -> str:
    return "secret_reference" if name.endswith("_REF") else _KIND[kind]


def _module_file(module: str) -> Path | None:
    base = _PACKAGE_ROOT.parent / module.replace(".", "/")
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _indirect_site(name: str, modules: Iterable[str]) -> list[str]:
    """Where a name read through a helper or composed key is written in source."""
    sites = []
    for module in modules:
        path = _module_file(module)
        if path is None:
            continue
        for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if f'"{name}"' in text or f"'{name}'" in text:
                rel = path.relative_to(_PACKAGE_ROOT.parent).as_posix()
                sites.append(f"{rel}:{number}")
                break
    return sites


@lru_cache(maxsize=1)
def _census() -> tuple[EnvironmentRead, ...]:
    return census(_PACKAGE_ROOT)


def _shape(d: EnvironmentDeclaration) -> tuple[object, ...]:
    return (d.type, d.required_when, d.default, d.sensitive)


def merge_declarations(
    candidates: Iterable[EnvironmentDeclaration],
) -> tuple[EnvironmentDeclaration, ...]:
    """Merge compatible declarations of one name (call sites union); conflicts refuse."""
    merged: dict[str, EnvironmentDeclaration] = {}
    for item in candidates:
        seen = merged.get(item.name)
        if seen is None:
            merged[item.name] = item
            continue
        if _shape(seen) != _shape(item) or seen.source_revision != item.source_revision:
            receipt = _refused(
                "conflicting_declaration", COMPONENT, item.source_revision
            )
            raise EnvironmentDeclarationError(
                "conflicting_declaration", receipt, item.name
            )
        sites = sorted({*seen.call_sites, *item.call_sites}, key=_site_key)
        merged[item.name] = EnvironmentDeclaration(
            seen.name, seen.component, seen.source_revision, seen.type,
            seen.required_when, seen.default, seen.sensitive, tuple(sites),
        )  # fmt: skip
    return tuple(sorted(merged.values(), key=lambda d: (d.component, d.name)))


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bound(read: EnvironmentRead, bindings: Iterable[EnvironmentReadBinding]) -> bool:
    return any(
        b.path == read.path and b.expression == read.expression and b.owner
        for b in bindings
    )


def _candidates(
    reads: tuple[EnvironmentRead, ...], revision: str, component: str
) -> list[EnvironmentDeclaration]:
    by_name: dict[str, list[str]] = {}
    for read in reads:
        if read.name:
            by_name.setdefault(read.name, []).append(read.call_site)
    out = []
    for name, kind, modules in _TABLE:
        sites = by_name.get(name) or _indirect_site(name, modules)
        out.append(
            EnvironmentDeclaration(
                name,
                component,
                revision,
                _table_type(name, kind),
                "optional",
                None,
                name in _SENSITIVE,
                tuple(sorted(sites, key=_site_key)),
            )
        )
    return out


def _conditions_resolve(items: Iterable[EnvironmentDeclaration]) -> str | None:
    listed = list(items)
    names = {d.name for d in listed}
    for d in listed:
        match = _CONDITION.match(d.required_when)
        if match and match.group(1) not in names:
            return d.name
    return None


def environment_declarations(
    source_revision: str,
    dynamic_bindings: Iterable[EnvironmentReadBinding] = (),
    *,
    component: str = COMPONENT,
    root: Path | None = None,
    extra: Iterable[EnvironmentDeclaration] = (),
) -> EnvironmentDeclarationExport:
    """Export the value-free declarations, or raise with a receipt that carries no value.

    ``root`` and ``extra`` let a conformance run census another tree and add candidate
    declarations; production callers pass neither.
    """
    bindings = tuple(dynamic_bindings)
    extra = tuple(extra)
    if component != COMPONENT:
        raise EnvironmentDeclarationError(
            "unknown_component",
            _refused("unknown_component", component, source_revision),
            component,
        )
    if source_revision != __version__:
        raise EnvironmentDeclarationError(
            "source_revision_mismatch",
            _refused("source_revision_mismatch", component, source_revision),
            "source_revision",
        )
    reads = census(root) if root is not None else _census()
    declared = {name for name, _kind, _modules in _TABLE} | {d.name for d in extra}
    unbound = [
        r
        for r in reads
        if r.kind == "read"
        and (
            (r.name is None and not _bound(r, bindings))
            or r.name not in {None, *declared}
        )
    ]
    if unbound:
        sites = [r.call_site for r in unbound]
        receipt = _refused("unresolved_dynamic_read", component, source_revision, sites)
        raise EnvironmentDeclarationError("unresolved_dynamic_read", receipt, sites[0])
    items = merge_declarations(
        [*_candidates(reads, source_revision, component), *extra]
    )
    dangling = _conditions_resolve(items)
    if dangling:
        receipt = _refused("unresolved_condition", component, source_revision)
        raise EnvironmentDeclarationError("unresolved_condition", receipt, dangling)
    rows = [
        [d.name, d.type, d.required_when, d.default, d.sensitive, list(d.call_sites)]
        for d in items
    ]
    census_rows = [[r.path, r.line, r.name, r.kind] for r in reads]
    receipt = EnvironmentDeclarationReceipt(
        "exported", component, source_revision, SCHEMA_VERSION, len(items),
        _digest(rows), _digest(census_rows), (),
    )  # fmt: skip
    return EnvironmentDeclarationExport(items, receipt)


# Every read whose name is not a literal, bound to who supplies the name. A pass-through
# reader (no inputs) serves names its callers write as literals, which the census sees.
SDK_READ_BINDINGS: tuple[EnvironmentReadBinding, ...] = (
    EnvironmentReadBinding(
        "scion_sdk/auth/browser.py",
        "self.api_key_env_var",
        "connector",
        ("{DECLARED_INPUT}",),
    ),
    EnvironmentReadBinding("scion_sdk/auth/declare.py", "name", "scion-sdk", ()),
    EnvironmentReadBinding(
        "scion_sdk/auth/declare.py", "url_env", "connector", ("{DECLARED_INPUT}",)
    ),
    EnvironmentReadBinding(
        "scion_sdk/auth/declare.py",
        "allow_plaintext_env",
        "connector",
        ("{DECLARED_INPUT}",),
    ),
    EnvironmentReadBinding("scion_sdk/config/__init__.py", "key", "scion-sdk", ()),
    EnvironmentReadBinding(
        "scion_sdk/config/instances.py",
        "f'{prefix.upper()}_INSTANCES'",
        "connector",
        ("{PREFIX}_INSTANCES",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/config/instances.py",
        "f'{prefix.upper()}_DEFAULT_INSTANCE'",
        "connector",
        ("{PREFIX}_DEFAULT_INSTANCE",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/config/instances.py",
        "f'{prefix}_ACCESS_HEADERS'",
        "connector",
        ("{PREFIX}_ACCESS_HEADERS",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/config/instances.py",
        "f'{prefix}_BASE_URL'",
        "connector",
        ("{PREFIX}_BASE_URL",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/config/instances.py",
        "f'{prefix}_ROUTE_HOST'",
        "connector",
        ("{PREFIX}_ROUTE_HOST",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/config/instances.py",
        "f'{prefix}_TLS_PROFILE_REF'",
        "connector",
        ("{PREFIX}_TLS_PROFILE_REF",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/credentials/resolver.py",
        "reference.target",
        "connector",
        ("{DECLARED_INPUT}",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/credentials/runtime_references.py",
        "rendered.removeprefix('env://')",
        "connector",
        ("{DECLARED_INPUT}",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/httpsupport/auth.py",
        "f'{prefix}_ACCESS_HEADERS'",
        "connector",
        ("{PREFIX}_ACCESS_HEADERS",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/ingest/privacy.py", "setting_name", "scion-sdk", ()
    ),
    EnvironmentReadBinding(
        "scion_sdk/library_state.py", "base_setting", "scion-sdk", ()
    ),
    EnvironmentReadBinding(
        "scion_sdk/mcp/network_inputs.py", "setting_name", "scion-sdk", ()
    ),
    EnvironmentReadBinding("scion_sdk/mcp/parser.py", "key", "scion-sdk", ()),
    EnvironmentReadBinding("scion_sdk/mcp/tool_surface.py", "toggle", "scion-sdk", ()),
    EnvironmentReadBinding("scion_sdk/mcp/upstreams/loader.py", "key", "scion-sdk", ()),
    EnvironmentReadBinding(
        "scion_sdk/mcp/upstreams/loader.py",
        "f'{REGISTRY_SETTING_PREFIX}_FILE'",
        "connector",
        ("{PREFIX}_FILE",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/mcp/upstreams/loader.py",
        "f'{REGISTRY_SETTING_PREFIX}_DISABLED'",
        "connector",
        ("{PREFIX}_DISABLED",),
    ),
    EnvironmentReadBinding("scion_sdk/paths/__init__.py", "variable", "scion-sdk", ()),
    EnvironmentReadBinding("scion_sdk/paths/__init__.py", "override", "scion-sdk", ()),
    EnvironmentReadBinding("scion_sdk/paths/_platform.py", "name", "scion-sdk", ()),
    EnvironmentReadBinding("scion_sdk/privacy_rules.py", "name", "scion-sdk", ()),
    EnvironmentReadBinding("scion_sdk/resilience.py", "name", "scion-sdk", ()),
    EnvironmentReadBinding(
        "scion_sdk/source_connectors/http_safety.py", "name", "scion-sdk", ()
    ),
    EnvironmentReadBinding("scion_sdk/tls/selection.py", "key", "scion-sdk", ()),
    EnvironmentReadBinding(
        "scion_sdk/writeback_sinks/_env.py", "name", "scion-sdk", ()
    ),
    EnvironmentReadBinding(
        "scion_sdk/writeback_sinks/runner.py",
        "sink.enable_flag",
        "connector",
        ("{DECLARED_INPUT}",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/writeback_sinks/spec_link.py",
        "f'{target.upper()}_ACT_AS_USER'",
        "scion-sdk",
        ("{TARGET}_ACT_AS_USER",),
    ),
    EnvironmentReadBinding(
        "scion_sdk/writeback_sinks/spec_link.py",
        "f'{target.upper()}_ENABLE_WRITE'",
        "scion-sdk",
        ("{TARGET}_ENABLE_WRITE",),
    ),
)


def declarations() -> tuple[EnvironmentDeclaration, ...]:
    """Every declared environment name of the installed SDK, sorted by name."""
    return environment_declarations(__version__, SDK_READ_BINDINGS).declarations


def startup_admission(
    export: EnvironmentDeclarationExport,
    environment: Mapping[str, str],
    *,
    enabled: bool = True,
) -> tuple[str, ...]:
    """Refusals (``NAME: code``) for a proposed environment; empty means admitted.

    A disabled component needs no input. Required inputs must be set and typed values
    must coerce; the check reads only the mapping it is given and opens nothing.
    """
    if not enabled:
        return ()
    problems: list[str] = []
    for d in export.declarations:
        raw = environment.get(d.name)
        condition = _CONDITION.match(d.required_when)
        required = d.required_when == "always" or (
            condition is not None and bool(environment.get(condition.group(1)))
        )
        if not raw:
            if required:
                problems.append(f"{d.name}: missing_required_input")
            continue
        if d.type == "int" and not re.fullmatch(r"-?\d+", raw):
            problems.append(f"{d.name}: invalid_value")
        if d.type == "bool" and raw.lower() not in _BOOLEANS:
            problems.append(f"{d.name}: invalid_value")
    return tuple(sorted(set(problems)))


_BOOLEANS = frozenset({"1", "0", "true", "false", "yes", "no", "on", "off"})
