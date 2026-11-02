# SCION-CONNECTOR-CONTROL research: upstream MCP registry

This file records what the registry rows (`SCION-CONNECTOR-CONTROL-R914`) rely on. It describes capabilities, not products.

## Capabilities of the MCP framework the SDK pins

- A proxy provider takes a client factory and forwards tool, resource, template and prompt calls to another MCP server. It caches component lists for 300 seconds by default and can disable the cache.
- An aggregate provider combines providers. It adds a namespace to a provider's components. Its error strategy is `warn` (log and skip) or `raise` (fail the operation).
- The namespace transform prefixes a tool with `<namespace>_` and puts the namespace in resource URIs after the scheme.
- A visibility transform hides components by tag, name or key. A request context can enable or disable components for one session and then notifies that session of the list change.
- The proxy client forwards elicitation, sampling, roots, logging and progress from the upstream to the connected client. By default it opens a fresh upstream session for each request.
- The stdio client transport keeps its subprocess alive between sessions unless `keep_alive` is off.

## Findings

- A hidden provider is still asked to list its components. Without a gate, one list call would start every stdio upstream. The registry needs a gate that answers empty until a session activates the namespace.
- The framework's elicitation forwarding raises when the front client cannot elicit. The registry must turn that into an explicit `decline`, so the upstream returns its own refusal.
- Tool names collide across upstreams (`execute`, `save`, `export`, `screenshot`), so a namespace is required, not optional.
- The SDK already owns the pieces the registry reuses: secret references, TLS profiles, egress checks, the tool fingerprint and readiness. The registry adds no parallel copy.

## Decisions

See the decision paragraphs in `plan.md` under "Upstream MCP registry (R914)": pooled lifecycle, lazy activation, `<namespace>_<tool>` naming, pass-through confirmation.

## Open items

None.
