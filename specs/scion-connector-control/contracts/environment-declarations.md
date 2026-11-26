# Environment declaration seam contract

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R053.3
Consumer: arbora ARBORA-HOST-R010
Consumer: arbora ARBORA-HOST-R010.2
Consumer: arbora ARBORA-HOST-R010.3

Counterpart: arbora `specs/host/contracts/environment-declarations.md`.

## Wire shape

The SDK exports value-free declarations for every environment name it reads, through `scion_sdk.environment.environment_declarations`. Each declaration holds `name`, `component`, `source_revision`, `type`, `required_when`, `default`, `sensitive`, `call_sites` and `schema_version`. The component is `scion-sdk`. The type is one of `string`, `int`, `bool`, `secret_reference` or `enum`.

The host composes the declarations of the SDK and of the connector, and owns startup admission (ARBORA-HOST-R010.3). The SDK never imports the host.

## Refusal codes

The export refuses with the closed codes defined in `SCION-CONNECTOR-CONTROL-R053.4`. A diagnostic never carries a value.

## Authoritative types

[`scion_sdk/environment.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/scion_sdk/environment.py) and [`scion_sdk/environment_census.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/scion_sdk/environment_census.py).

## Test vectors

[`tests/test_environment_manifest_served.py`](https://github.com/Knuckles-Team/scion-sdk/blob/main/tests/test_environment_manifest_served.py)::`test_model_rejects_duplicates_and_credential_defaults`.
