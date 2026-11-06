# GIT-2 — restore the recorded WorkSession commit

The 0.1.1 restore preview and confirmation token identified snapshot.oid, but
WorkSessionHistory.materialize resolved snapshot.ref again. Moving the ref before
or after preview silently restored different file contents. Both public-RPC
regression cases failed before the fix: reviewed content became unreviewed.

Materialization now receives the recorded commit OID directly. Ref movement no
longer changes the selected tree. An absent recorded OID refuses with
CHECKPOINT_NOT_FOUND rather than resolving a mutable name.

The regression checks both timings, the restored file, unchanged HEAD, unchanged
staged index tree, and a safety snapshot containing the exact pre-restore staged
content. Tests use temporary repositories and the public WorkSession RPCs.

Verification: 22 focused restore/integration/session/recovery/boundary tests passed.
Final hostrun check exited 0: ruff, types, architecture and contracts passed;
the known ownership substep emits Not a git repository and remains unvalidated.
The full core/contracts and pure IPC-client suite passed before this isolated
materialization change; the 22 focused tests were run after it.
No installed application restart, package rebuild, or remote push was performed.

Continuation: GIT-1 and GIT-2 go to human review. Next priorities remain SEC-11
(subject to path locks), session-aware AUD-1/CORE-6, then native-CLI handoff and
REL-4 upgrade verification. Completed local fixes do not turn the two failed
installed-restart artifacts into successful acceptance.
