# Conformance evidence contract

Provider: medulla MEDULLA-TYPED-PACKS-R807
Consumer: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R041

## Wire shape

`connector-certify` submits one `ConformanceEvidence` record through the Medulla client adapter. The record carries the tool fingerprints, the pack digest and the immutable revision of the connector. The read method returns the record that was submitted.

## Refusal codes

- Engine unavailable: the command exits non-zero and writes the report locally without a submission.
- Unknown field in the record: the engine refuses it, as the engine contract refuses unknown fields.

## Owner rows

- Provider: medulla MEDULLA-TYPED-PACKS-R807.
- Consumer: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R041; the reader is arbora ARBORA-OPS-R067.10.
