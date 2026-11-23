# A2A storage and streaming contract

Provider: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R017.1
Consumer: arbora ARBORA-A2A-R001.3

## Wire shape

`create_a2a_app` accepts a `Storage` protocol with `list_contexts(caller,
cursor, limit)`. The app serves `tasks/list` through it and returns one
page with a continuation cursor.

`message/stream` runs the turn as a durable background job and streams its
turn events to the caller as server-sent events, one `data:` frame per
event. The four event kinds are `text-delta`, `tool-call`, `tool-result`,
and `final-state`; each frame carries `kind`, `taskId`, `contextId` and a
`seq` that is the 1-based position in the task's durable turn log. Every
event is appended to the log before it is sent, so a caller that drops the
connection loses nothing: `tasks/resubscribe` with an `afterSeq` cursor
replays the events with sequence greater than the cursor, once, in order,
from the storage (row R017.2, consumer ARBORA-A2A-R001.4.1). A replay is a
snapshot of the log at the moment it is served; a caller resubscribes again
to follow a task that is still running.

## Storage turn-event API

The same `Storage` protocol also carries the turn log:
`append_turn_event(task_id, event) -> seq` appends one wire event (without
`seq`) to the task's log and returns the sequence the storage assigned;
`turn_events_after(caller, task_id, after_seq) -> [event]` returns the
task's events with sequence greater than `after_seq`, in sequence order,
each carrying `seq`.

## Refusal codes

- `-32001`: the caller names a context it does not own.
- A cursor from another caller is refused with the same code and no data.
- `tasks/resubscribe` refuses a task the caller does not own, and a task
  that is not visible to it, with the same code and no data, so task
  existence never leaks. Malformed resubscribe params (missing or
  out-of-range `id`, `caller`, or `afterSeq`) refuse with `-32602`.

## Owner rows

- Provider: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R017.1 and SCION-CENTRALIZED-CAPABILITIES-R017.2.
- Consumer: arbora ARBORA-A2A-R001.3 and arbora ARBORA-A2A-R001.4.1.
