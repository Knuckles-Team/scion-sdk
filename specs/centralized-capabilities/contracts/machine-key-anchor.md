# Machine key anchor function

Provider: medulla MEDULLA-MACHINE-R007
Consumer: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R057.1

## Wire shape

The SDK calls `medulla.engine.machine_key_ref(kind, raw)`. It returns JSON `{"iri","key"}`. The `iri` is the key anchor id `urn:machine-key:<kind>:<32 hex>`. The SDK passes the kind as `uuid`, `serial`, `bmc` or `host`.

## Refusal codes

- `ValueError("<kind>:<reason>")` with reason `malformed`, `placeholder` or `empty`. The SDK raises `MachineKeyRefused`.

## Authoritative types

- `crates/medulla-pyengine/src/machine.rs` in medulla.
- `scion_sdk/machine.py`.

## Test vectors

- The five key anchor vectors in the `data-model.md` of `MEDULLA-MACHINE`.
- `tests/test_machine_observer.py::test_observes_machine_targets_the_key_anchor_of_the_vector`.
