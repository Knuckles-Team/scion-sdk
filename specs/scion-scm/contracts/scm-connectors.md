# Contract: SCM connectors implement the change-proposal contract

Provider: scion-sdk SCION-SCM-R003.2
Consumer: github-agent CONN-GITHUB-AGENT-CONNECTOR-CONTENT-R007.13
Consumer: gitlab-api CONN-GITLAB-API-R004.12

Each SCM connector implements `ScmEventNormalizer` and maps its replies to the records of SCION-SCM-R003.1. It flags every merge or arm tool with `owner_approval_required` (SCION-SCM-R003.3), as CONN-GITHUB-AGENT-CONNECTOR-CONTENT-R007.8 and CONN-GITLAB-API-R004.7 do.

## Wire shape

- `normalize(headers: Mapping[str, str], body: bytes) -> ScmFeedbackEvent`.
- `verify_webhook(scheme: "hmac_sha256" | "shared_token", secret_ref, headers, body) -> None`.
- Manifest entry `{"owner_approval_required": true}` on a merge or arm operation.

## Refusal codes

- `SCM_SIGNATURE_INVALID`: the signature or token is missing or wrong.
- `SCM_EVENT_UNSUPPORTED`: the event type has no feedback kind.
- `TOOL_APPROVAL_FLAG_MISSING`: a merge or arm method lacks the flag at registration.
- `approval_required`: a flagged tool was called without `approval_ref`.

## Authoritative types

- `scion_sdk/scm/records.py` and `scion_sdk/scm/events.py`.

## Test vectors

- `tests/test_scm_contract_served.py::test_two_fixture_connectors_normalize_and_flag_merge_tools`.
