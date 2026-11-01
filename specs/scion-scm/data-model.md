# SCION-SCM — Data model

The change-proposal contract (SCION-SCM-R003.1) defines these frozen records in `scion_sdk/scm/records.py`.

| Record | Fields | Closed sets |
|---|---|---|
| `ChangeProposalRef` | provider, repository, number, head_sha, base_ref, state, author, changed_lines | state: `open`, `closed`, `merged` |
| `ReviewRecord` | id, proposal, reviewer, reviewer_kind, state, commit_sha, submitted_at, run_key | reviewer_kind: `person`, `automated`; state: `approved`, `changes_requested`, `commented`, `dismissed` |
| `ReviewThreadRecord` | id, proposal, path, line, resolved, comments | none |
| `CheckRunRecord` | id, proposal, head_sha, name, status, conclusion, url | status: `queued`, `in_progress`, `completed` |
| `ScmFeedbackEvent` | event_id, provider, kind, proposal, actor, actor_kind, at, body_digest | kind: `check_changed`, `review_submitted`, `review_comment`, `conversation_comment`, `proposal_merged`, `proposal_closed`, `proposal_reopened`, `head_pushed` |

`proposal` fields hold a `ChangeProposalRef`. `run_key` groups the findings of one automated review run. `body_digest` is the SHA-256 of the raw webhook body; the body is never stored.
