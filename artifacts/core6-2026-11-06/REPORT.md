# CORE-6 — resource maintenance

Baseline: a286858. Date: 2026-11-06. MCP plan-1, node-50.

## Changes

- Retention transactionally records expired legacy checkpoint refs with their
  repository path in a durable deletion outbox (migration 0007). The daemon
  processes at most 16 jobs per maintenance pass and acknowledges only successful
  deletions. Repository failures retain the job for retry across restart. Only
  refs/aconsole/checkpoints/<tab>/<number> is eligible; user branches are refused.
- Session-owned refs are protected in the persisted queue and immediate checkpoint
  rotation. WorkSession snapshots, archived recovery refs and session-linked tabs
  have no automatic history GC. This deliberately preserves the plan's existing
  session-history policy; it does not bound all retained user history.
- Claude/Codex usage polling seeks from the previous byte offset. It retains model
  and token-total dedup state, handles partial JSON lines, valid EOF without newline,
  inode rotation and observed truncation. Unchanged files read zero bytes. A broader
  time window rereads history; custom reader callbacks preserve their full-read
  contract. Partial lines are capped at 2 MiB; appends are read in 64 KiB chunks.
  This assumes append-only CLI logs between rotations, not arbitrary in-place edits.
- The real core entrypoint configures private rotating logs without the UI:
  core.log, nominal 4 MiB per file, three backups. Uvicorn inherits this logger.
  Private directory/file modes are 0700/0600. Rotation is tested during one process.
- SQLite autocheckpoint is 1000 pages with journal_size_limit 4 MiB. Periodic
  maintenance requests TRUNCATE outside an ORM transaction with zero lock wait.
  A pinned reader defers truncation and triggers a warning; a later pass compacts
  after release without interrupting readers or losing committed rows. These
  settings are not a hard WAL size cap while an indefinitely held reader pins it.

## Validation

All commands use HOSTRUN_CWD=/home/sas/Projects/sp_aconsole_prototype.

- Initial resource regressions: four failures reproduced full-file rereads and
  missing ref/WAL/logging maintenance. An additional regression reproduced immediate
  rotation deleting a protected session ref before that path was repaired.
- hostrun test core tests/core/test_resource_retention.py
  tests/core/test_opencode_cursor.py tests/core/test_persistence_a.py
  tests/core/test_persistence_c.py tests/core/test_round6_k22.py: exit 0, 82 tests.
- hostrun test core: exit 0. Known forkpty deprecation warnings remain.
- hostrun test contracts: exit 0, nine tests.
- After adding the daemon integration regression, hostrun test core
  tests/core/test_resource_retention.py: exit 0, nine tests. It connects to the
  serving daemon, calls app/snapshot over IPC and verifies the persisted deletion
  job is drained without an internal maintenance call.
- Final hostrun check: Ruff, strict types, six architecture contracts, raw SQL,
  API and contract checks passed. The ownership substep still reports
  "Not a git repository"; it is not evidence that ownership was validated.
- git diff --check: clean. No UI/layout changes or installed user-app restart.

The SQLite pinned-reader probe was added to test_persistence_a.py, the existing
engine boundary test file expressly permitted to issue SQL. No existing test was
altered to change its assertions. The new sqlite3 fetchone typing escape in
engine.py is limited to the untyped library boundary and followed by row validation.

## Acceptance

Implementation is submitted for human verification, not self-accepted as done.
A hard disk quota for all WorkSession history or a policy to terminate indefinitely
held readers would require a separate explicit retention decision.
