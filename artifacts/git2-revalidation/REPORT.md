# GIT-2 — acceptance revalidation

Existing implementation: 0b7717f; no product changes were needed for this node.

WorkSessionHistory.materialize passes target.oid directly to snapshots.materialize and refuses a missing recorded OID with CHECKPOINT_NOT_FOUND rather than resolving target.ref.

Both public-RPC cases in test_work_session_restore_integrity.py move the ref before or after restorePreview. Restore applies the reviewed recorded content, keeps HEAD and the staged index tree unchanged, and records a safety snapshot containing the exact pre-restore staged content. These are real temporary Git repositories and daemon IPC calls; fake container plumbing does not replace Git or the public restore implementation.

The original failure analysis is retained in artifacts/plan-reconciliation-0.1.1/RESTORE.md. Its former universal human-review barrier is superseded by kb-127/PLAN-3; this revalidation does not claim a new native agent or desktop deployment.

Validation: all relevant cases are included in `hostrun test core`, exit 0, 1807 tests; see artifacts/sec11/full.txt. The same source also passes hostrun check (known ownership warning is not a validated ownership check). The 71-test focused Git/MCP run is separately documented, without claiming it includes every case named here. Existing tests were neither edited nor skipped.

No legacy GIT-2 phase-gate PASS is claimed. Acceptance uses the stated regression/integration evidence; installed application activation remains a separate task.
