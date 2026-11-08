# GIT-1 — acceptance revalidation

Existing implementation: 4c9b15a; no product changes were needed for this node.

Four public RPC cases in test_work_session_result_integrity.py replace the recorded result ref after workSession/result and test session/generic Git preview and integrate. Each receives MERGE_PREVIEW_STALE, leaves the session HEAD unchanged and preserves unreviewed worker bytes. Restoring the original ref permits only the originally recorded result; a later worker commit still prevents cleanup.

WorkSessionGit compares the requested and preview OIDs against snapshot.oid. Generic Git paths also validate session snapshot metadata. The test uses real temporary repositories, IPC and SQLite, with no installed application restart.

The review of 82eaa78/af92e2e/e36ce9c and revised ownership/continuation order remains recorded in artifacts/plan-reconciliation-0.1.1/REVIEW.md. Its old human-review requirement is superseded by kb-127/PLAN-3; native CLI criteria remain separate. REL-1/REL-2 mechanisms survive the new paths, CORE-1 shutdown still drains work, and HAR-2 retains its independent native hook acceptance requirement. Next priority is the confirmed SEC-11 race, followed by remaining core latency/IPC and native-CLI tasks. The failed security diagnostic is not reclassified as accepted by this review.

Validation: all relevant cases are included in `hostrun test core`, exit 0, 1807 tests; see artifacts/sec11/full.txt. The same source also passes hostrun check (known ownership warning is not a validated ownership check). The 71-test focused Git/MCP run is separately documented, without claiming it includes every case named here. Existing tests were neither edited nor skipped.

No legacy GIT-1 phase-gate PASS is claimed. Acceptance uses the stated regression/integration evidence; installed application activation remains a separate task.
