# REL-4 — acceptance revalidation

Existing implementation: b73d4b2. No product code or existing test changed for this node.

The safe, explicit refusal branch of acceptance is implemented: a daemon lacking work_sessions.v1 yields the localized PROTOCOL_VERSION_UNSUPPORTED error. The client disconnects and releases pending transport resources; it does not send initialized, snapshots or WorkSession requests over an incompatible connection. Automatic reconnect stops after refusal; explicit retry succeeds once the required capability is available.

Four regressions in test_core_upgrade.py use separate disposable Python processes, real Unix sockets/flock and a sleeping disposable agent. They verify initial refusal, explicit retry, graceful replacement timeout and automatic reconnect against a legacy handshake. The old daemon and agent stay alive, their lock stays held, no replacement is spawned on initial refusal, and a binary uncommitted-data sentinel remains unchanged. The fixture's own final process cleanup is distinct from the application's refusal behavior.

The detailed failure analysis remains in artifacts/rel4-2026-11-06/REPORT.md. Its former human-review submission is superseded by kb-127/PLAN-3. This revalidation does not claim automatic legacy upgrade or native agent CLI certification, and it does not restart the user's application.

Validation: every named case is included in the current `hostrun test core`, exit 0, 1811 tests; raw output is in artifacts/core2/full.txt. The same source passes hostrun check with the known unvalidated ownership warning. The 53-case focused CORE-2 command is reported separately, not represented as covering these additional modules. No native GUI/CLI run, installed restart or unsupported legacy REL-4 phase-gate PASS is claimed.
