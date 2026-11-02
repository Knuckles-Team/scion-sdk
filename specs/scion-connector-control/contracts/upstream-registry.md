# Upstream registry seam contract

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R914
Consumer: craft-suite-api CONN-CRAFT-SUITE-API-APPS-R002

Counterpart: craft-suite-api `specs/craft-suite-apps/contracts/upstream-registry.md`.

## Wire shape

The consumer declares upstreams in code. It sets `Connector.upstreams` to a tuple of `UpstreamDecl` values (`SCION-CONNECTOR-CONTROL-R914.1`). Each value holds `name`, `namespace`, `kind`, `command`, `args`, `sandbox`, `activation` and the optional `expected_fingerprint`. An operator overlay file named by `SCION_UPSTREAMS_FILE` merges by `name` (`SCION-CONNECTOR-CONTROL-R914.6`).

The SDK serves the result on the consumer's own server. A proxied tool is `<namespace>_<tool>`. Every proxied component carries the tag `ns:<namespace>` and the metadata key `scion/upstream`. The server adds the tools `registry_list_namespaces`, `registry_activate`, `registry_deactivate` and `registry_status`.

## Refusal codes

`UPSTREAM_DECL_INVALID`, `UPSTREAM_KIND_FIELDS`, `UPSTREAM_NAMESPACE_INVALID`, `UPSTREAM_NAMESPACE_DUPLICATE`, `UPSTREAM_ROOTS_REQUIRED`, `UPSTREAM_ROOT_UNSAFE`, `UPSTREAM_ROOT_OVERLAP`, `UPSTREAM_SECRET_LITERAL`, `UPSTREAM_EGRESS_REFUSED`, `UPSTREAM_BRIDGE_NOT_LOOPBACK`, `UPSTREAM_BRIDGE_TOKEN_MISSING`, `UPSTREAM_TOOL_COLLISION`, `UPSTREAM_STRATEGY_INVALID`, `UPSTREAM_NAMESPACE_UNKNOWN`, `UPSTREAM_DISABLED`, `UPSTREAM_ACTIVE_CAP`, `UPSTREAM_CIRCUIT_OPEN`, `UPSTREAM_POOL_FULL`, `UPSTREAM_SPAWN_FAILED`, `UPSTREAM_DRIFT_REFUSED`. The consumer adds no code of its own for these cases.
