# SCION-CONNECTOR-CONTROL — SDK connector control, certification, and pack publication

**Requirement IDs:** SCION-CONNECTOR-CONTROL-R001 through SCION-CONNECTOR-CONTROL-R054.
**Owner:** scion-sdk.
**Boundary:** This SDK owns source-side connector hosting, discovery, certification, pack construction, and submission. A graph service owns durable pack records and receipts; a control-plane service owns routing and activation policy. Vendor packages own their API calls.

## Outcome and scope

A connector package can use one SDK server factory, manifest, credential reference, HTTP/TLS stack, certification command, and generated graph client. Its published pack and tool pins are reproducible from the connector artifact. The runner refuses uncertified or mismatched packages. Every migrated connector builds without importing `agent_utilities`. This is an SDK contract, not a requirement to copy any private fleet inventory.

The migration is five independently reviewable alphabetical batches: A–E (SCION-CONNECTOR-CONTROL-R009), F–J (SCION-CONNECTOR-CONTROL-R010), K–O (SCION-CONNECTOR-CONTROL-R011), P–S (SCION-CONNECTOR-CONTROL-R012), and T–Z (SCION-CONNECTOR-CONTROL-R013). Packages in a batch are selected from public connector repositories at implementation time, not from a hard-coded count. Those packages own their migration commits. SCION-CONNECTOR-CONTROL-R014 removes an old duplicate toolkit in the control-plane repository after consumers have migrated. SCION-CONNECTOR-CONTROL-R015 establishes the SDK as source lifecycle and certification owner while retiring the duplicate there. Connector base settings move into `scion_sdk/config.py`. Deployment settings and agent/model settings stay with their owners (SCION-CONNECTOR-CONTROL-R016).

## Architecture and contracts

```text
vendor connector package ──manifest and entry point──▶ SDK discovery/certify
        │                                          │ exact package tuple, tool schemas
        └──FastMCP tools/resources/prompts─────────┤
                                                   ▼
                          SDK artifact capture → canonical ConnectorPack
                                                   │ generated client + idempotency
                                                   ▼
                                  durable pack authority and receipt
```

1. A connector manifest names its package identity, server identity, tool pins, capabilities, credential references, source streams, and artifact annotations. Validate closed kinds, sorted unique entries, URI schemes, tenant scope, and no literal secrets before opening a session. Reuse `manifest/model.py`, `loader.py`, `live_contract.py`, `tool_schema.py`, `artifacts/pack.py`, and `certify/`; extend them instead of adding a parallel schema or publisher.
2. Certification binds **entry-point group + name + distribution + version**, runtime MCP server identity, tool name, normalized input schema digest, normalized output schema digest, and declared capability. A missing output schema is still fingerprinted canonically, never treated as an empty wildcard. Normalize schemas through `manifest/tool_schema.py`; SDK conformance tests own the pin calculation. The graph service stores a `contract_pin` as a claim attribute and must not be asked to recompute SDK normalization.
3. Pack publication uses the graph service's generated `ConnectorPack` and `AgentComponent.Content` request/response types. SDK forms a deterministic idempotency key over tenant, connector identity, canonical pack digest, and operation. A retry of an identical request returns the same durable result. Pack entry bodies remain content-addressed and bounded. No hand-written duplicate wire DTO and no placeholder content pack are allowed.
4. SQL-backed source record submission uses the generated `SourceIngest` request, checkpoint and receipt types through the existing SDK sink. The adapter declares table/key identity, change operation, schema digest, source version and provenance. The runner submits bounded pages only after live contract validation. The accepted receipt, not local success, advances the cursor. Reject null or mismatched tenant, schema, primary key and checkpoint fields before the graph call.
5. Pack annotations are a closed, canonical map per entry: capability names, input and output schema digests, modality, bounded cost and latency estimates with units, and provenance. Missing estimates mean `unknown`, never zero. The SDK validates types and units, excludes presentation-only fields from contract fingerprints, and includes semantic annotation changes in the pack digest. Re-certifying the fleet means each package produces current nonempty fingerprints for every declared pin and passes a current live-contract comparison; a package count alone is insufficient.
6. `mcp/server.py`, `mcp/action_dispatch.py`, `auth/`, `http/`, `tls/`, `credentials/`, and `ports/` remain the sole shared connector toolkit. `env://` and `openbao://` references may appear in manifests; resolved values may appear only in request memory, never logs, exceptions, packs, or reports. Unknown auth, public network exposure, or uncertified package tuple fails closed.
7. Connector-internal tool choice or inbound event triage may request a proposal from a decision service. This proposal is observational only. It never directly authorizes a side effect or bypasses deterministic write-back rules.
8. The release contract locks a published compatible graph-client wheel. An isolated installed consumer verifies its generated types, native kernels, and error catalog. A source overlay, unpinned wheel, or stale lock cannot replace the release dependency.
9. Every connector package performs HTTPS calls through the SDK's shared TLS profile in `tls/`; no connector may disable certificate verification with a bare boolean flag or silence the underlying TLS warning library (SCION-CONNECTOR-CONTROL-R017). The SDK's full test suite, strict type checking, and documentation build must pass cleanly against the connector control implementation from a fresh checkout, with no partial or broken module left in the merged source tree (SCION-CONNECTOR-CONTROL-R018).
10. `register_tool_surface` must register one typed, annotated tool per API operation by default, without environment mode selection. Action-dispatch tools are an opt-in compatibility mode. R026 derives the operation tools and R032 preserves explicit additional registration.

## Portable development and contribution

Clone this public repository, install `uv` and Python matching `pyproject.toml`, run `uv sync`, then run the tests in `test-spec.md`. Unit tests use in-memory fake MCP sessions and fake generated-client ports. No tenant, hosted graph, secret store, or private connector fleet is needed. A contributor can add one synthetic fixture connector in `tests/fixture_package` and prove manifest, pin, pack, and failure behavior. Live publication is a later acceptance gate with a contributor-owned environment.

## Completion rule

Delivery evidence requires an exact merged commit that contains the required implementation and checks. Acceptance requires the gates in `test-spec.md` to pass against that commit. The evidence section below must link a published wheel and a real connector pack receipt. Current implementation exists in `manifest/`, `certify/`, `mcp/`, `artifacts/`, and `runner/`, but fleet cutover and release acceptance are unverified here.

### Evidence

| Gate | Evidence |
| --- | --- |
| Exact merged SDK implementation | Pending exact commit |
| Five batch consumer conformance | Pending batch receipts |
| Published-wheel isolated consumer | Pending artifact digest and CI run |
| Live pack publication and idempotent retry | Pending redacted receipt |

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.

## Access contracts (SCION-CONNECTOR-CONTROL-R021)

Each connector ontology declares how a consumer reads each class live. The `ac:` vocabulary in `scion_sdk/ontology/access_contract.ttl` defines `ac:AccessContract` and `ac:PropertyBinding`. A contract names the class, the source, the entity and the key field. It names the access kind: `mcp_tool`, `http_endpoint`, `graphql_query` or `a2a_skill`. It names the operation, its parameters, the pagination mode and the pushdown operations. Pushdown operations are `filter`, `sort`, `limit` and `project`. `ac:authRef` holds an `env://` or `openbao://` reference, never a secret. Rate limit, freshness and cost hints let a consumer choose a live read or a copied hot slice. A missing hint means unknown.

`parse_access_contracts` reads the flat Turtle profile and returns typed `AccessContract` values. `virtual_mapping_fields` returns the agent-utilities `VirtualMapping` arguments. arbora onboarding imports these contracts as unapproved mappings. MEDULLA-UNIFIED-DATA-PLANE-R037/R038 and MEDULLA-FEDERATED-QUERY-R072/R073 consume the result.

## Pack-scoped ontology imports (SCION-CONNECTOR-CONTROL-R022)

medulla accepts an `owl:imports` only when it names an ontology entry of the same pack.
Fleet ontologies import shared hub ontologies such as `http://knuckles.team/kg`.
The tenant schema composes every attached pack, so the hub import is redundant at pack scope.
`build_content_pack` removes each import outside the pack from every ontology body.
It records each removed IRI as `ontology-import:<iri>` in the entry's `requires_capabilities` annotation.
The builder handles Turtle and RDF/XML bodies.
A body without external imports stays byte-identical.

## Prompt capture with required arguments (SCION-CONNECTOR-CONTROL-R023)

A prompt with no required argument is captured by rendering it, unchanged from prior behavior.
A prompt with a required argument is captured as a template instead of refused.
`PromptArtifactKind` binds each required argument to a deterministic `{{name}}` placeholder.
It calls `prompts/get` with those bound placeholders and leaves optional arguments unbound.
The captured body records `capture.kind` as `template` and `capture.bound_arguments` as the placeholder map.
`build_content_pack` treats a template capture the same as a rendered capture; the pack entry body is opaque bytes to the archive.
A prompt onboarding path that previously raised `MalformedArtifactError` for a required argument now succeeds.

## Prompt capture when the server rejects the bound placeholder (SCION-CONNECTOR-CONTROL-R024)

A typed required prompt argument (`int`, `float`, `bool`, `list`, or an enum) can refuse the string `{{name}}` placeholder SCION-CONNECTOR-CONTROL-R023 binds for it.
Capture must never depend on the server accepting a placeholder value for a typed argument.
`McpClientSession.get_prompt` distinguishes a well-formed MCP protocol error from every other failure.
A protocol error (`mcp.shared.exceptions.MCPError`) raises `McpPromptRejectedError`, a subclass of `McpTransportError`.
Every other failure, including a transport failure such as a timeout or dropped connection, still raises the plain `McpTransportError` it always has.
`PromptArtifactKind.list_entries` catches `McpPromptRejectedError` only for a prompt that bound at least one required argument.
On that catch it builds the captured `result` from the prompt's own listed name and arguments alone, with no server-rendered content, and keeps `capture.kind` as `template`.
A prompt with no required argument never bound a placeholder, so the same error there is a genuine rejection and still raises.
Any failure that is not `McpPromptRejectedError` always raises, so a server that is truly unreachable still fails the capture.

## Caller-scoped context and progress

SCION-CONNECTOR-CONTROL-R050 defines the shared context for every registered tool.
Callers receive ordered progress without polling and a correlated terminal result.
A call without a progress token emits no progress notification.
Context state is a disposable request cache; durable knowledge remains owned by EG.
EG owns approval decisions. Arbora serves the authenticated approval exchange and returns EG's decision unchanged.
The SDK checks the decision's binding before a source effect.
Reconnect recovery refuses when its authenticated durable result producer is unavailable.
Acceptance requires the served integration cases CC-50 through CC-55.


## Hosted credential binding

R035.1 through R035.6 require exact host-authorized tenant/client/resource bindings for every secret reference, including process-visible environment values.
R046 uses the same host-owned challenge and vault authority for bounded inline PKCE, without blocking stdin or pasted-code files.
ARBORA-IDENTITY-R023 defines the matching consumer; qualified grant, challenge and persistence ports remain missing integration.
All added or refined work requires served integration acceptance.

<!-- spec-definition-patch:sdk5-r007-major-version-gate -->
## Major-version compatibility receipt and pre-pin gate (R007.1-R007.6)

A major-version rollout is one transition over the complete authoritative connector-package inventory. Bind the transition to the distribution, old and target major pins, immutable target artifact digest, and canonical inventory snapshot. Bind each sorted package entry to its repository, source commit, current pin, and dependency-file preimage digest. Hash canonical entries with SHA-256. The producer stops if it cannot list every package.

Evaluate the target artifact in an isolated candidate environment for every package. Do not change tracked manifests or lockfiles. Emit one completed `PackageCompatibilityReceipt` per package. Bind each receipt to the transition, inventory, package, source commit, and old and target pins. Also bind its preimage, artifact, complete check suite, run, and outcome. Import scans alone do not prove compatibility.

Use this producer-to-consumer order:

1. `scion_sdk.testing.fleet_migration.evaluate_major_version_compatibility(inventory, transition)` produces receipts for the complete inventory.
2. `authorize_major_version_pin_changes(transition, inventory, receipts)` validates exact membership and freshness. It authorizes only a complete, passing set.
3. Each package writer calls `require_package_pin_change(gate, package_id, pin_preimage)` before its first pin write. R009-R013 use this same gate.

The gate checks transition, artifact, inventory, package source, current pin, and preimage. Fleet preflight refuses missing, duplicate, extra, failed, incomplete, cancelled, stale, or mismatched receipts before the first pin write. That refusal preserves every pin byte-for-byte. Each writer rechecks package authorization and preimage before its first write. A race may reject package B after package A commits. B's writer refuses its write and reports exact applied/refused package IDs. The partial outcome requires reconciliation. Align it with `PIPE-RELEASE-R001.8` through `PIPE-RELEASE-R001.12`. Do not claim the system restored A's previous pin. One package's receipt cannot authorize another package.

**Implementation boundary:** `scion_sdk.testing.fleet_migration.scan_many_packages` and `PackageScanResult` currently report import-scan findings only. They do not produce compatibility receipts, evaluate a target artifact, authorize rollout, or gate pin writes. The producer, gate, rollout command, and R009-R013 writer attachments remain unimplemented. R007.6 describes future proof; this definition claims no runtime behavior or test result. Existing R007 fingerprint and live-contract checks, plus R009-R013 migration obligations, remain in force.
<!-- /spec-definition-patch -->

<!-- spec-definition-patch:sdk-consolidated-primary-20261012-spec -->

## Sandbox allocation producer for AU:29 (R051.1-R051.6)

The SDK provisions and reconciles admitted sandbox requests through one typed port. Arbora owns host admission and the certified support matrix. Source trace: `AU:29 49df21c60c947240d4f02b1424b1de29183f10a69c76bd885f19d13e7237061c`.

**SCION-CONNECTOR-CONTROL-R051.1 — Model.** Define SandboxProvisioner. It consumes Arbora ProvisionRequest. It returns ProvisionResult with allocation ID, actual instances, observed image/platform, state, timestamps, and provider receipt.

**SCION-CONNECTOR-CONTROL-R051.2 — Behavior.** Allocate once per stable operation ID. Reconcile uncertain results before retry. Transport loss never proves no allocation.

**SCION-CONNECTOR-CONTROL-R051.3 — Wiring.** Plan scion_sdk.ports.sandbox.SandboxProvisioner. Route AU sandbox access through Arbora agents.run and NativeWorkItemAdmissionProtocol/CapacityLedger. Call the SDK adapter, then return its receipt to Arbora. This producer and binding are absent.

**SCION-CONNECTOR-CONTROL-R051.4 — Refusal.** Refuse unsupported profiles, missing grants, expired requests, and conflicting replay IDs before provider calls. Reconcile unresolved outcomes before retry. Unsupported combinations allocate nothing.

**SCION-CONNECTOR-CONTROL-R051.5 — Evidence.** Bind request/result digests, adapter version, matrix revision, operation/allocation IDs, instances, state, timestamps, and reconciliation. Exclude credentials and secret values.

**SCION-CONNECTOR-CONTROL-R051.6 — Hot-path proof.** Future served tests enter Arbora agents.run. Use a disposable certified adapter. Observe instances and independent readback. Test unsupported profiles and response loss without duplicates.

## Visual observation transport for AU:35 (R052.1-R052.6)

The SDK transports visual observations and provenance between steps. EG owns durable perception state; AU owns next-step conditioning. SDK request_cache remains request-scoped. Source trace: `AU:35 9da1fe3853d82945989df4194b626b793a37937ff892b483c1583a8d1aae644e`.

**SCION-CONNECTOR-CONTROL-R052.1 — Model.** Define VisualObservationEnvelope. Include observation ID, modality, source operation, and frame or sequence. Bind capture time, content digest/reference, tenant/run/request scope, and schema revision. Keep payload behind governed references.

**SCION-CONNECTOR-CONTROL-R052.2 — Behavior.** Pass ordered observation references to the next authorized step in the same run. EG owns durable storage and retention. SDK request_cache is not cross-request memory.

**SCION-CONNECTOR-CONTROL-R052.3 — Wiring.** Bind create_mcp_server tools/call through tool_surface or method_tools and dispatch_async. Forward the observation to EG state, then AU. The current SDK path lacks this producer and binding.

**SCION-CONNECTOR-CONTROL-R052.4 — Refusal.** Refuse expired, invalidated, uncommitted, unauthorized, cross-tenant/run, and wrong-schema references before provider calls. Never substitute another request's cache.

**SCION-CONNECTOR-CONTROL-R052.5 — Evidence.** Bind the digest, source/frame, producer operation, tenant/run, schema revision, retention decision, and consumer step. Exclude raw visual content and secrets.

**SCION-CONNECTOR-CONTROL-R052.6 — Hot-path proof.** Future served tests cross MCP tools/call, EG state write/readback, and AU next-step use. Prove same-run reuse, cross-run isolation, and stale-reference refusal.

## SDK environment declaration export

R053 defines the planned public environment_declarations operation in scion_sdk.config.
It exports only SDK-owned declarations and deterministic census evidence.
Arbora composes the host manifest and owns all startup validation and admission decisions.
The SDK must preserve configuration precedence, typed coercion and value-free diagnostics.
The host composer and AU producer remain separate prerequisites.
R053's six children define model, behavior, wiring, refusal, evidence and served acceptance.
No current export or successful served startup is claimed.

## JSON tag-loader migration

The SDK owns the migration contract in SCION-CONNECTOR-CONTROL-R054.1 through SCION-CONNECTOR-CONTROL-R054.6.
A contributor must inventory pinned source loaders before replacing their registrations.
The served source and replacement surfaces must preserve operation, schema, tag, authorization and refusal behavior.
The SDK must preserve the explicit R026 and R032 registration opt-ins.
Unknown loaders or unavailable source evidence must block replacement.
The SDK owns the reusable gate; each connector owns its startup binding and backend fixture.
EG policy and Arbora admission remain outside this migration contract.

Success requires the complete served matrix in test-spec.md and independent receipt reconstruction.
A spec definition, import scan or isolated helper test supplies no runtime acceptance.

## Consolidated packet trace

SDK:5 remains defined by R007.1-R007.6. No duplicate SDK:5 rows were added. `AU:29 49df21c60c947240d4f02b1424b1de29183f10a69c76bd885f19d13e7237061c`; `AU:35 9da1fe3853d82945989df4194b626b793a37937ff892b483c1583a8d1aae644e`; `GRAPHOS:9 cfd8f1270b5ec1f19d8c78f50e5a833695573763a691584e8c74d8fa5f1cb225`.

The current SDK tree lacks these four producers and bindings. This packet adds definitions only. It adds no runtime implementation or acceptance receipt.
<!-- /spec-definition-patch -->

<!-- spec-definition-patch:sdk-confirmation-r026-1 -->
## SCION-CONNECTOR-CONTROL-R026.1 — Destructive confirmation across supported MCP eras

**Model.** Bind each confirmation to its canonical operation and JSON arguments. Modern requests use one named elicitation form. Require a boolean `confirmed` field and a SHA-256 request-state binding. Legacy elicitation uses the same field with strict boolean validation.

**Behavior.** Select the path from the live negotiated protocol. For 2026-07-28 and later, return an input-required result. Continue only when a matching retry accepts the form and supplies `confirmed is True`. For supported handshake-era versions, call `Context.elicit` with a `StrictBool` response model. Accept only when `confirmed is True`. The pending modern result converts to false in boolean contexts. This keeps legacy `if not await` guards fail-closed. SDK dispatchers propagate the pending result and proceed only on exact `True`. Never retry through another protocol after refusal or failure.

**Wiring.** The shared gate is `scion_sdk/mcp/context.py::ctx_confirm_destructive`. `action_dispatch.dispatch_async` and generated method dispatch pass operation arguments to that gate. Both dispatchers propagate pending results. They call `invoke_client_method` only after explicit acceptance.

**Explicit confirmation.** Every destructive generated tool takes an optional boolean `confirm` (absent when the operation already declares one), and `dispatch_async` and `ctx_confirm_destructive` take the same explicit flag. Only an exact `True` on this call confirms, with or without authentication, on stdio and HTTP alike. A client that did not advertise elicitation is never sent an input request it cannot answer. It receives `{"cancelled": true, "operation": ..., "reason": ...}`, where the reason names `confirm=true`.

**Refusal.** Refuse before the connector effect when context or protocol identity is missing or capability is unsupported. Refuse false, nonboolean, and missing fields. Refuse decline, cancellation, binding mismatch, transport error, or malformed response. Nondestructive operations never ask for confirmation.

**Evidence.** Record the negotiated protocol and exact FastMCP and MCP versions with served `tools/call` results. Test `tests/test_mcp_confirmation_roundtrip.py` through `HTTPX MockTransport`. Do not call a live provider. Preserve the R026 landing receipt. R026.1 starts with no landed or verified receipts.

**Hot-path proof.** Exercise generated per-method registration and `action_dispatch` separately in both protocol eras. Explicit acceptance yields exactly one controlled backend request. False, truthy false, nonboolean, decline, cancellation, missing content, or elicitation failure yields zero requests. Test missing or unversioned context, the legacy boolean-guard regression, and nondestructive calls without elicitation. Helper-only tests do not replace served calls.
<!-- /spec-definition-patch -->



## SCION-CONNECTOR-CONTROL-R026.2 — Keyword-named manifest fields

An OpenAPI field such as `from` cannot be a Python parameter. The manifest names it
`python_name: from_`. The generated tool declares `from_` with a pydantic field alias
`from`. The tool schema and calls use `from`, and the client receives `from_`.
## API-key cookie isolation (SCION-CONNECTOR-CONTROL-R028.3)

The public SDK authentication strategies include header-based API keys, bearer tokens, client credentials, and delegated tokens; they document no provider cookie-session authentication. Public mentions of cookies or sessions refer to MCP/source sessions, not provider identity. Isolation therefore applies only to ApiKeyAuth and preserves existing behavior for other auth strategies.

The data path stays in the existing client factory and governed transport. HTTPX may add explicit or jar-seeded cookies while building a request. The sync or async governed transport strips Cookie before sending each request to the inner transport. It strips response Set-Cookie before returning the response to HTTPX. That prevents providers from seeding the shared API-key client's jar in any response status.

The configured API-key header remains the sole request identity. The policy does not follow redirects, change the destination, relax TLS, alter retries, retain provider session credentials, or create a second client or session wrapper. A connector that requires cookie-session authentication needs a separate explicit SDK auth contract.

## User Scenarios & Testing

### User Story 1 - SDK connector control, certification, and pack publication (Priority: P1)

A connector author or host integrator relies on the SDK for this capability: A connector package can use one SDK server factory, manifest, credential reference, HTTP/TLS stack, certification command, and generated graph client.

**Acceptance Scenarios**:

1. **Given** a connector or host that uses the SDK capability named in SCION-CONNECTOR-CONTROL-R001, **When** it exercises "Pack retries reuse one key", **Then** the test bound to that row passes and the refusals the row names are typed.
2. **Given** any active row of this spec, **When** the pre-push hooks run, **Then** its caller chain, shape and test are named in requirements.md and the row has a task line.

### Edge Cases

- Refuse incomplete migration scans: the row SCION-CONNECTOR-CONTROL-R014.28 states the refusal or bound and its test proves it.
- A prompt the server rejects for its bound placeholder still captures as a template: the row SCION-CONNECTOR-CONTROL-R024 states the refusal or bound and its test proves it.
- Work that needs an authenticated principal refuses typed: the row SCION-CONNECTOR-CONTROL-R025.4 states the refusal or bound and its test proves it.
- No actor bootstrap: an unbound context resolves from the transport: the row SCION-CONNECTOR-CONTROL-R025.5 states the refusal or bound and its test proves it.
- The rate limiter keys on the bound actor; the trusted local principal is not throttled: the row SCION-CONNECTOR-CONTROL-R025.6 states the refusal or bound and its test proves it.
- A connector declares its outbound auth in one line and the SDK builds the governed client: the row SCION-CONNECTOR-CONTROL-R028.4 states the refusal or bound and its test proves it.

## Requirements

- **FR-001**: Pack retries reuse one key. Rows: SCION-CONNECTOR-CONTROL-R001.
- **FR-002**: Contract pin travels as a claim attribute, not recomputed downstream. Rows: SCION-CONNECTOR-CONTROL-R002.
- **FR-003**: Canonical output-schema digest on every certified tool verdict. Rows: SCION-CONNECTOR-CONTROL-R003.
- **FR-004**: Contract-pin verification owned by the SDK's own test kit. Rows: SCION-CONNECTOR-CONTROL-R004.
- **FR-005**: Pack publisher and source submission use generated graph-client types. Rows: SCION-CONNECTOR-CONTROL-R005.
- **FR-006**: Decision-service proposals for tool choice and triage stay observational. Rows: SCION-CONNECTOR-CONTROL-R006.
- **FR-007**: Fleet-wide re-certification yields nonempty schema fingerprints (rollup). Rows: SCION-CONNECTOR-CONTROL-R007.
- **FR-008**: Closed, canonical pack annotation map across the connector fleet. Rows: SCION-CONNECTOR-CONTROL-R008.
- **FR-009**: Connector batch A-E migrates fully onto the SDK client. Rows: SCION-CONNECTOR-CONTROL-R009.
- **FR-010**: Connector batch F-J migrates fully onto the SDK client. Rows: SCION-CONNECTOR-CONTROL-R010.
- **FR-011**: Connector batch K-O migrates fully onto the SDK client. Rows: SCION-CONNECTOR-CONTROL-R011.
- **FR-012**: Connector batch P-S migrates fully onto the SDK client. Rows: SCION-CONNECTOR-CONTROL-R012.
- **FR-013**: Connector batch T-Z migrates fully onto the SDK client. Rows: SCION-CONNECTOR-CONTROL-R013.
- **FR-014**: SDK fully supersedes the duplicated connector toolkit (rollup). Rows: SCION-CONNECTOR-CONTROL-R014.
- **FR-015**: SDK is sole owner of connector source lifecycle and certification. Rows: SCION-CONNECTOR-CONTROL-R015.
- **FR-016**: Connector base settings live in the SDK's own config module. Rows: SCION-CONNECTOR-CONTROL-R016.
- **FR-017**: Every connector uses the shared TLS profile, no bare verify bypass. Rows: SCION-CONNECTOR-CONTROL-R017.
- **FR-018**: SDK source and test suite build cleanly from a fresh checkout. Rows: SCION-CONNECTOR-CONTROL-R018.
- **FR-019**: Host servers register ontology resources without duplicate skills or prompts. Rows: SCION-CONNECTOR-CONTROL-R019.
- **FR-020**: One tool per API operation is the default. Rows: SCION-CONNECTOR-CONTROL-R020.
- **FR-021**: Connector ontologies declare access contracts for virtual reads. Rows: SCION-CONNECTOR-CONTROL-R021.
- **FR-022**: The pack builder scopes owl:imports to the pack. Rows: SCION-CONNECTOR-CONTROL-R022.
- **FR-023**: A prompt with a required argument becomes a template. Rows: SCION-CONNECTOR-CONTROL-R023.
- **FR-024**: A prompt the server rejects for its bound placeholder still captures as a template. Rows: SCION-CONNECTOR-CONTROL-R024.
- **FR-025**: One actor-resolution and entitlement path for every transport. Rows: SCION-CONNECTOR-CONTROL-R025.
- **FR-026**: SDK derives one typed tool per client method. Rows: SCION-CONNECTOR-CONTROL-R026.
- **FR-027**: SDK resolves the selected provider-runtime profile for a multi-backend connector. Rows: SCION-CONNECTOR-CONTROL-R027.
- **FR-028**: SDK's governed HTTP client controls egress with named escape hatches, not ad hoc trust. Rows: SCION-CONNECTOR-CONTROL-R028.
- **FR-029**: SDK exposes a structured configuration singleton, not only a flat setting() lookup. Rows: SCION-CONNECTOR-CONTROL-R029.
- **FR-030**: SDK provides GraphQL introspection context helpers for connector tools. Rows: SCION-CONNECTOR-CONTROL-R030.
- **FR-031**: SDK's client-credentials token provider accepts a literal client secret, matching the signature connectors already call. Rows: SCION-CONNECTOR-CONTROL-R031.
- **FR-032**: Explicit additional registration. Rows: SCION-CONNECTOR-CONTROL-R032.
- **FR-033**: SDK's governed HTTP layer provides a safe_post_json helper with the same private-host allowance as its GET path. Rows: SCION-CONNECTOR-CONTROL-R033.
- **FR-034**: SDK provides shared library-file-path resolution and cross-process file locking utilities. Rows: SCION-CONNECTOR-CONTROL-R034.
- **FR-035**: SDK provides a credential provider boundary scoped to the requesting connector (rollup). Rows: SCION-CONNECTOR-CONTROL-R035.
- **FR-036**: SDK's identity-scoped resource entitlements cover the backend-resource catalogs these connectors manage. Rows: SCION-CONNECTOR-CONTROL-R036.
- **FR-037**: erpnext supplier and purchase-order operation contract is typed and recorded. Rows: SCION-CONNECTOR-CONTROL-R037.
- **FR-038**: uptime service-health operation contract is declared. Rows: SCION-CONNECTOR-CONTROL-R038.
- **FR-039**: container-manager deployed-workload operation contract. Rows: SCION-CONNECTOR-CONTROL-R039.
- **FR-040**: SDK declares an MCP source's access contract. Rows: SCION-CONNECTOR-CONTROL-R040.
- **FR-041**: SDK declares an A2A source's access contract. Rows: SCION-CONNECTOR-CONTROL-R041.
- **FR-042**: SDK declares an API (REST) source's access contract. Rows: SCION-CONNECTOR-CONTROL-R042.
- **FR-043**: SDK declares a GraphQL source's access contract. Rows: SCION-CONNECTOR-CONTROL-R043.
- **FR-044**: SDK declares a virtual-graph access contract. Rows: SCION-CONNECTOR-CONTROL-R044.
- **FR-045**: Tool contract facts for the run loop and the tool-subset decision (rollup). Rows: SCION-CONNECTOR-CONTROL-R045.
- **FR-046**: Browser authorization-code flow with PKCE as a reusable auth manager (rollup). Rows: SCION-CONNECTOR-CONTROL-R046.
- **FR-047**: Agent-card discovery from the standard well-known path. Rows: SCION-CONNECTOR-CONTROL-R047.
- **FR-048**: Agent-readable text form in tool contracts (rollup). Rows: SCION-CONNECTOR-CONTROL-R048.
- **FR-049**: Budget-limited, approval-gated payment challenge handling (rollup). Rows: SCION-CONNECTOR-CONTROL-R049.
- **FR-050**: Caller-scoped tool context and ordered progress (rollup). Rows: SCION-CONNECTOR-CONTROL-R050.
- **FR-051**: Native desktop operation port with a no-cursor, no-focus invariant. Rows: SCION-CONNECTOR-CONTROL-R060.
- **FR-052**: Release integrity binds the live declaration and ontology to a pinned signer (rollup). Rows: SCION-CONNECTOR-CONTROL-R650.
- **FR-053**: Sandbox allocation through one typed port (rollup). Rows: SCION-CONNECTOR-CONTROL-R051.
- **FR-054**: Visual observation transport between ordered steps (rollup). Rows: SCION-CONNECTOR-CONTROL-R052.
- **FR-055**: SDK environment declaration export (rollup). Rows: SCION-CONNECTOR-CONTROL-R053.
- **FR-056**: JSON tag-loader migration (rollup). Rows: SCION-CONNECTOR-CONTROL-R054.
- **FR-057**: Connector health separates not-configured from unavailable and gates alerts on enabled (rollup). Rows: SCION-CONNECTOR-CONTROL-R065.
- **FR-058**: Each connector serves its seed graph and contract resources at first connect (rollup). Rows: SCION-CONNECTOR-CONTROL-R068.
- **FR-059**: Connector transports are declared contracts per connection protocol (rollup). Rows: SCION-CONNECTOR-CONTROL-R069.
- **FR-060**: Declared semantic coverage refusal. Rows: SCION-CONNECTOR-CONTROL-R700.
- **FR-061**: Source credentials by source name (rollup). Rows: SCION-CONNECTOR-CONTROL-R900.
- **FR-062**: Identifier gate and datapoint signing (rollup). Rows: SCION-CONNECTOR-CONTROL-R901.
- **FR-063**: Connector tools declare and require an acting user (rollup). Rows: SCION-CONNECTOR-CONTROL-R902.
- **FR-064**: The SDK authors each connector pack as Turtle from one typed declaration (rollup). Rows: SCION-CONNECTOR-CONTROL-R067.
- **FR-065**: SDK helpers replace the retired agent-utilities harness modules (rollup). Rows: SCION-CONNECTOR-CONTROL-R780.
- **FR-066**: Training-trajectory recorder helper. Rows: SCION-CONNECTOR-CONTROL-R781.
- **FR-067**: Current-plan boundary models. Rows: SCION-CONNECTOR-CONTROL-R070.
- **FR-068**: One declared connector identity. Rows: SCION-CONNECTOR-CONTROL-R071.
- **FR-069**: Certify names the connector from its declaration. Rows: SCION-CONNECTOR-CONTROL-R072.
- **FR-070**: The server factory serves all five connector surfaces and keeps the parent link. Rows: SCION-CONNECTOR-CONTROL-R073.
- **FR-071**: A connector pack declares every surface and the source contracts for virtual access (rollup). Rows: SCION-CONNECTOR-CONTROL-R074.
- **FR-072**: The connector's MCP server is the only place its metadata is read from (rollup). Rows: SCION-CONNECTOR-CONTROL-R075.
- **FR-073**: Ordered equivalent fallback applies the ranked path order and reports outcomes (rollup). Rows: SCION-CONNECTOR-CONTROL-R904.
- **FR-074**: Offline teacher score producer. Rows: SCION-CONNECTOR-CONTROL-R905.
- **FR-075**: SDK provisioner: allocate and reconcile instances with actual receipts. Rows: SCION-CONNECTOR-CONTROL-R906.
- **FR-076**: Connector pack carries a seed graph resource. Rows: SCION-CONNECTOR-CONTROL-R907.
- **FR-077**: Export the connector environment declarations. Rows: SCION-CONNECTOR-CONTROL-R908.
- **FR-078**: Define the native desktop action port. Rows: SCION-CONNECTOR-CONTROL-R909.
- **FR-079**: Define the typed observation producer contract. Rows: SCION-CONNECTOR-CONTROL-R910.
- **FR-080**: Tool declaration of a resource protocol. Rows: SCION-CONNECTOR-CONTROL-R911.
- **FR-081**: Virtual-graph SQL and document dialect declaration. Rows: SCION-CONNECTOR-CONTROL-R912.
- **FR-082**: One capability contract unifies a skill resource and a tool, and a frontend contribution declares its read model. Rows: SCION-CONNECTOR-CONTROL-R913.

## Success Criteria

- **SC-001**: Every active row is proven by its bound test and the pre-push gates pass. Rows: SCION-CONNECTOR-CONTROL-R001.

## Assumptions

- The owners named in each row (Medulla, Arbora, the connectors) provide their side of the seam; this spec owns only the SDK side.
- Tests run offline against synthetic fixtures after `uv sync`; no private service is required.
