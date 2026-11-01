# A2A storage and streaming contract

Provider: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R017.1
Consumer: arbora ARBORA-A2A-R001.3

## Wire shape

`create_a2a_app` accepts a `Storage` protocol with `list_contexts(caller, cursor, limit)`. The app serves `tasks/list` through it and returns one page with a continuation cursor. `message/stream` emits text-delta, tool-call, tool-result and final-state events. `tasks/resubscribe` replays events after an `after_seq` cursor kept in the storage (row R017.2, consumer ARBORA-A2A-R001.4.1).

## Refusal codes

- `-32001`: the caller names a context it does not own.
- A cursor from another caller is refused with the same code and no data.

## Owner rows

- Provider: scion-sdk SCION-CENTRALIZED-CAPABILITIES-R017.1 and SCION-CENTRALIZED-CAPABILITIES-R017.2.
- Consumer: arbora ARBORA-A2A-R001.3 and arbora ARBORA-A2A-R001.4.1.
