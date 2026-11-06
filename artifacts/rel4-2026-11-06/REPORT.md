# REL-4 — safe refusal of an incompatible core

Baseline: 2d2e329. MCP plan-1, node-106. Date: 2026-11-06.

## Reproduction

The current UI requires work_sessions.v1 during initialize. A legacy daemon lacks
that capability, so start_daemon raises before returning server_pid to the UI's
replacement logic. The existing localized PROTOCOL_VERSION_UNSUPPORTED message
is appropriate, but the rejected connection remained open with a live reader.
An explicit retry overwrote the client's connection references without closing
the previous connection. All three process regressions reproduced that defect.

## Resolution and decision

Keep the explicit, localized incompatibility refusal rather than automatically
stop a daemon whose agents and upgrade safety have not been established.
No initialized notification, app snapshot or WorkSession request is sent after
an incompatible handshake. The client releases the socket, reader and pending
resources without signalling the daemon. Automatic reconnect terminates on a
handshake refusal; an explicit connect/start_daemon can retry later.

Client disconnect is separate from close's existing owned-process lifecycle.
The disconnect helper excludes its current task so refusal from inside reconnect
does not cancel or await itself. A daemon that ignores a requested graceful
replacement remains alive with its lock; the existing bounded fallback then
reports incompatibility without a force kill or second spawn.

No capability downgrade, server PID trust extension, forced restart, UI view
change, or installed application restart is introduced. The preexisting diagnostic
already states that UI and core versions are incompatible and the matching core
must be restarted. Automatic upgrade of a legacy daemon remains unavailable;
the node's safe, testable refusal acceptance branch is implemented.

## Evidence

- hostrun test core tests/core/test_core_upgrade.py: three product failures
  before the fix (open transport, orphaned retry connection, open timeout fallback).
  Earlier test-harness setup errors were corrected before recording this RED run.
- hostrun test core tests/core/test_core_upgrade.py
  tests/core/test_core_recovery.py tests/gui/test_core_client.py: exit 0, 29 tests.
- Four new cases cover initial refusal, explicit retry, graceful replacement
  timeout, and automatic reconnect encountering a legacy core. They run separate
  disposable Python processes speaking the legacy initialize protocol, hold a
  real flock and use isolated short Unix-socket paths. The agent is a disposable
  sleeping process; this is not native agent CLI compatibility certification.
- Initial refusal sends only initialize then disconnects. Reconnect sends no
  initialized or data request after the refused handshake. Old core and agent
  stay alive, lock remains held, replacement marker stays absent, and the
  uncommitted binary data sentinel remains byte-identical.
- Existing real daemon crash/recovery tests also pass; no user daemon was touched.
- Final hostrun check: Ruff, strict types, architecture and contract checks pass.
  Its known ownership substep emits Not a git repository despite overall exit 0;
  ownership has not been validated by that substep.
- git diff --check: clean. Existing tests/assertions were not modified.

Implementation is submitted as verifying for human acceptance.
