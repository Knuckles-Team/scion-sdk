"""Environment declarations of the SDK.

SCION-CONNECTOR-CONTROL-R908. ``declarations()`` lists every environment name the
SDK and its transports read, including indirect library reads, so a host or the
conformance kit can compare a connector's reads against it. The census test
scans the package and fails when a read is undeclared.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SCHEMA_VERSION", "EnvironmentDeclaration", "declarations"]

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EnvironmentDeclaration:
    """One environment name the SDK reads."""

    name: str
    type: str
    required_when: str
    default: str | None
    sensitive: bool
    call_sites: tuple[str, ...]


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


def declarations() -> tuple[EnvironmentDeclaration, ...]:
    """Every declared environment name, sorted by name."""
    return tuple(
        EnvironmentDeclaration(
            name=name,
            type=kind,
            required_when="optional",
            default=None,
            sensitive=name in _SENSITIVE,
            call_sites=sites,
        )
        for name, kind, sites in _TABLE
    )
