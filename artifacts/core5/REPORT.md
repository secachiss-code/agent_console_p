# CORE-5 — IPC and ingest resilience

Base: 09d93ea on shared main. Additive snapshot contract request C2 is included in this change.

## Behavior

- Malformed JSON and unexpected handler exceptions return typed PARSE_ERROR / INTERNAL_ERROR responses. Private exception details stay in logs. A subsequent valid request on the same socket succeeds. Oversized complete frames return MESSAGE_TOO_LARGE; a bounded discard recovers a line up to four transport limits. Unterminated/over-budget input may still be disconnected deliberately.
- Oversized live or replayed events become an explicit resync marker at the lost event's sequence. Publishing pauses until a new snapshot. The client automatically reloads state without reconnecting, including when the oversized event was the last event in the stream.
- Ingest overflow preserves the order of each tab's latest retained update; newer arrivals cannot overtake the coalesced backlog. After applying retained state, the consumer reports loss and the daemon publishes a visible warning plus resync. A failing item handler is also reported and does not stop subsequent items. Resync refreshes current state; it does not reconstruct missing action history.
- Requests admitted to the worker queue can be cancelled before dispatch, with REQUEST_CANCELLED and no later side effect. Already executing synchronous mutations drain normally; they are not forcibly interrupted.
- Large snapshots use app/snapshotChunk only after MESSAGE_TOO_LARGE and negotiation of snapshot.chunks.v1. The frozen AppSnapshot JSON is limited to 64 MiB, raw chunks to 1 MiB and the actual transport limit. Connection-local cursors expire after 30 seconds; expiration is enforced on access. Completion, expired access, replacement or connection teardown release the buffered bytes. The client checks sequence, total bytes, base64, cursor progress and completion before publishing state. Ordinary app/snapshot parameters and result remain unchanged.
- UI/core compatibility continues to use protocol and required capabilities, rather than exact application-version equality. Existing upgrade regressions verify the localized refusal of a core missing work_sessions.v1.

The additive DTOs, catalog, method registry, schemas, golden bytes, fake server, client bindings and generated typed facade are updated together. No new dependency or view/style change is included.

## Validation

- Tests preceded each implementation step. Baseline regressions reproduced handler/parse disconnects, silent event loss, overflow order, missing resync, missing snapshot chunks, queued mutation after timeout, and oversized-frame disconnect. A later injected ingest handler failure reproduced the missing loss notice. Initial red outputs and intermediate green runs are retained beside this report.
- Final focused command: hostrun test core tests/core/test_snapshot_chunks.py tests/core/test_snapshot_chunk_validation.py tests/core/test_queued_cancellation.py tests/core/test_transport_resilience.py tests/core/test_core_upgrade.py tests/gui/test_core_client.py tests/gui/test_typed_facade.py tests/contracts — exit 0, 60 cases.
- Full-suite regression run initially failed fake method coverage and pipelined pre-auth request ordering. The fake result catalog now covers the additive method; cancellation registration is enabled only after successful initialization, preserving ordered authorization errors. Existing tests were kept unchanged. See full-red.txt.
- hostrun test core — exit 0, 1835 cases; raw complete-suite output in full.txt.
- hostrun check — exit 0: Ruff, strict basedpyright (zero errors/warnings), six architecture contracts, ten contract checks and API floor pass. The known ownership substep prints “Not a git repository”; ownership is not claimed as validated.
- git diff --check — exit 0. Existing tests were not edited, skipped or weakened. Twenty-four new regression cases were added.

Integration tests run isolated real daemon sockets, HTTP ingest, SQLite and Git with fake container plumbing. They do not claim a native model session or installed desktop acceptance. The installed package remains ecac0a6; it was not restarted or deployed. Independent SEC-11 remains failed. No unsupported legacy CORE phase-gate PASS is claimed; completion follows MCP acceptance and PLAN-3.
