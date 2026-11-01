# Meristem seam contract

Provider: scion-sdk SCION-CONNECTOR-CONTROL-R067
Consumer: meristem MERISTEM-RUN-R016

Counterpart: [meristem specs/run/contracts/scion-sdk.md](https://github.com/Knuckles-Team/meristem/blob/main/specs/run/contracts/scion-sdk.md).

## Wire shape

No record crosses at runtime. The seam is an import boundary: a connector repository imports the connector SDK and never imports meristem, and meristem holds no connector, manifest, certification or ontology-pack code. The SDK owns source access, connector toolkit helpers, certification, manifests and ontology pack generation.

## Refusal codes

The meristem census reports a finding per connector checkout that imports a retired meristem module and exits non-zero. The SDK adds no refusal code at this seam.
