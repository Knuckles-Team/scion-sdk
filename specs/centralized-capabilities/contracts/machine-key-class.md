# Machine key anchor class

Provider: medulla MEDULLA-MACHINE-R004.3
Consumer: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R057.1

## Wire shape

The relation `observes` of a connector resource has target class `MachineKey` of the core ontology. The engine shape `MachineKeyShape` requires exactly one `machineKey` and one `identifies` link on each anchor. The SDK declares the relation with `OBSERVES_RELATION`.

## Refusal codes

- The engine refuses an anchor whose id does not match `^urn:machine-key:(uuid|serial|bmc|host):[0-9a-f]{32}$`.

## Authoritative types

- `crates/medulla-core/ontology/machine-v1.ttl` in medulla.
- `scion_sdk/manifest/model.py`: `ResourceRelation`.

## Test vectors

- `crates/medulla-core/tests/machine_vocab.rs::machine_key_anchor_shape_requires_one_machine_and_a_matching_iri` in medulla.
