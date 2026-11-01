# Contract: the engine stores feedback events as delivery records

Provider: scion-sdk SCION-SCM-R003.1
Consumer: medulla MEDULLA-SDLC-R019.5

The engine accepts each `ScmFeedbackEvent` through `DeliveryFeedback` and stores it as a `:FeedbackEvent` record. The `event_id` is the idempotency key.

## Wire shape

- `ScmFeedbackEvent{event_id, provider, kind, proposal, actor, actor_kind, at, body_digest}` as JSON.

## Refusal codes

- `FEEDBACK_SOURCE_UNKNOWN`: the engine knows no proposal for the event.
- A repeated `event_id` returns the first record and writes nothing.

## Authoritative types

- `scion_sdk/scm/records.py::ScmFeedbackEvent`.

## Test vectors

- `tests/test_scm_contract.py::test_records_are_frozen_and_closed_sets_refuse_unknown_values`.
