# Contract: arbora calls the normalize tool and honours the approval flag

Provider: scion-sdk SCION-SCM-R003.4
Consumer: arbora ARBORA-OPS-R896.8
Consumer: arbora ARBORA-OPS-R896.10

Arbora receives a provider webhook on its signed route. It calls the connector's `<prefix>_normalize_scm_event` tool through `fleet.call` and passes the event to the engine. Arbora calls a flagged merge tool only with an owner approval receipt as `approval_ref`.

## Wire shape

- Request: `{"headers": {...}, "body": "<base64 raw body>"}`.
- Response: one `ScmFeedbackEvent` as JSON.
- Flagged tool metadata: `meta.owner_approval_required = true`.

## Refusal codes

- `SCM_SIGNATURE_INVALID`, `SCM_EVENT_UNSUPPORTED`, `SCM_SECRET_UNSET`: no event results.
- `approval_required`: the flagged tool made no client call.

## Authoritative types

- `scion_sdk/scm/events.py::register_scm_normalizer`.

## Test vectors

- `tests/test_scm_contract.py::test_registration_adds_one_normalize_tool`.
