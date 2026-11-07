# Secret redaction seam with the secret-fields gate

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R025.7.2
Provider: scion-sdk SCION-CONNECTOR-CONTROL-R025.7.3
Consumer: pipelines PIPE-GATES-R097

## Wire shape

A field is secret-named when its name ends in a credential word (`token`, `secret`, `password`, `passwd`, `passphrase`, `api_key`, `apikey`, `private_key`, `access_key`, `secret_key`, `client_secret`, `credentials`, `connection_string`, `dsn`, `auth_header`, `cookie`, `session_key`), in snake or camel case, optionally followed by `value`, `plaintext` or `raw`. Flags (`has_`, `is_`, `requires_` prefixes) and cursor tokens (`continuation_token`, `page_token`, `fencing_token`, `cancellation_token`) are not. The SDK redacts secret-named values in every served tool result. A field the SDK may serve is marked in the JSON schema: `"x-redacted": true` (always redacted, model type `Redacted[str]`) or `"x-secret-bearing": true` (a newly minted secret returned once by the tool that declares it, model type `SecretBearing[str]`). A tool that returns a newly minted secret carries the SDK-derived annotation `returnsSecret: true` with `secretFields: [...]`; the gate accepts those output fields of that tool only. The gate reports a secret-named model field or schema property without one of these marks, `SecretStr`, `Field(exclude=True)` or `writeOnly`.

## Refusal codes

- Gate finding codes: `SF-MODEL-FIELD`, `SF-SCHEMA-FIELD`, `SF-SERVED-OUTPUT-FIELD`, `SF-RECORDED-VALUE`, `SF-RECORDED-FIELD`.
- SDK: a `secret_bearing` name that is not a field of the tool's output schema fails the server build with a typed error naming the tool.

## Authoritative types

- `scion_sdk/privacy_rules.py` `field_redaction` (the name rule) and `scion_sdk/mcp/secret_redaction.py` (planned: `SecretRedactionMiddleware`, `Redacted`, `SecretBearing`).
- pipelines `pipelines_hooks/connector/secret_scan.py` `is_secret_name`.

## Test vectors

- `tests/test_tool_response_redaction.py` (planned vectors for R025.7.2: `runners_token`, `runnersToken`, `token_expires_at`, `api_key_env`, `has_password`, `continuation_token`).
- pipelines `tests/hooks/test_secret_fields.py::test_secret_names_end_in_a_credential_word` holds the same vectors.

## Owner rows

- Provider: scion-sdk SCION-CONNECTOR-CONTROL-R025.7.2 and SCION-CONNECTOR-CONTROL-R025.7.3.
- Consumer: pipelines PIPE-GATES-R097.
