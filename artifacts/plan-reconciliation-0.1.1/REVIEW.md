# AConsole plan reconciliation after Git 0.1.1

Review date: 2026-11-06. Reviewed range: bbbc86a..45152a1.
Scope: lifecycle/ownership, Git result integration and cleanup, checkpoint deletion,
core shutdown/recovery, migrations/contracts, and recorded installation outcomes.
This is a focused integration review, not acceptance of all 141 changed files or
of the installed application.

## New commits

| Commit | Meaning for the existing plan |
| --- | --- |
| 82eaa78 | WorkSession owns workspace/branch/history; tabs become executors. Migrations 0005/0006 and work_sessions.v1 change lifecycle and IPC. Re-audit Git against session ownership, not the old tab model. |
| af92e2e | Local tests, benchmarks and package acceptance evidence. Native CLI session handoff remains untested in that report. |
| e36ce9c | Waiting executor tabs and checked handoff; preserve its WORKTREE_BUSY protections and new UI behavior. |
| f4c8ce9 | Records installation/source equality, but says restart outcome is recorded separately. |
| 45152a1 | Both restart artifacts are failed (graceful-stop timeout; version/capability mismatch). Installation equality is not proof that the new running UI/core pair is healthy. |

Earlier fixes remain present: descriptor-relative restore deletion (REL-1),
ignored-data preservation (REL-2), worker/writer shutdown drain and runtime
credentials (CORE-1/CORE-3), verified Codex hooks (HAR-2). These mechanisms are
retained and their regression tests rerun with WorkSession. Closing a tab now
intentionally retains its primary session workspace.

## Confirmed Git defect and correction (GIT-1)

WorkSessionHistory records snapshot.oid, but preview followed the mutable public
snapshot.ref without comparing the resolved commit to that recorded OID. Moving
the ref after workSession/result made both workSession/integratePreview and
Git's generic mergePreview successfully preview a different tree as the old result.
Two new RPC regression cases failed before the fix; stale-after-preview cases
already refused safely.

Preview/merge now validate session-ref OIDs against the durable snapshot metadata,
including the generic Git RPC path. WorkSession integration also validates its
requested source OID against the selected recorded result. A changed ref returns
MERGE_PREVIEW_STALE; the application does not silently repair refs or accept new
worker changes. Restoring the original ref allows the original recorded result to
be accepted. Later worker commits prevent cleanup and remain on disk.

## Revised continuation order

1. GIT-1: finish immutable-result validation and its RPC/data-preservation tests.
2. GIT-2: pin session history restore to its recorded OID as well; test moved
   refs before and after restorePreview. This related path uses target.ref today.
3. SEC-11: close the confirmed mutable-config/driver execution race when its path
   locks allow a claim. Name scans are insufficient. Do not accept verifying nodes
   or bypass UX-7 locks to make it claimable.
4. AUD-1: audit WorkSession integrate/restore/handoff/cleanup with session-owned
   workspaces, pending journal recovery, archived sessions, and isolated results.
   REL-1/REL-2 stay required regression gates. Reproduce REL-3 before editing:
   descriptor-relative unlink already removes dangling symlink leaves.
5. CORE-6: design retention for session history explicitly. The new implementation
   deliberately retains session snapshot refs and session-linked closed tabs;
   never apply tab-only GC to them or delete an archived task's only recovery refs.
6. HAR-3/HAR-4: distinguish work_session_id from native CLI conversation IDs;
   test resume/fork and status per executor without violating the one-writer rule.
7. REL-4 (new): test upgrade from a core lacking work_sessions.v1. The client checks
   that capability before start_daemon returns the server PID to the UI's restart
   decision. Use disposable processes, not the user's live application.
8. QA-3/QA-6: native CLI relay/integration acceptance remains distinct from the
   earlier HAR-2 hook trial and from fake-container tests.

Plan-1 gained GIT-1, GIT-2 and REL-4. Existing verifying nodes remain for human acceptance;
no previous acceptance or failure artifact was rewritten. Follow-up notes for
session-aware retention/audit are recorded as plan knowledge.

## Verification

- RED: new RPC cases returned successful previews for replaced refs (2 failures);
  stale-after-preview and preservation cases already passed (2 passes).
- After fix: 61 focused tests passed across result integrity, WorkSession/recovery,
  restore boundaries, ignored worktrees, merge, core recovery and Codex hook runtime.
- Static check initially exposed two new-test style issues and an existing stale
  linked generated types.md after 0.1.1. New tests were formatted and the document
  regenerated without changing API/schema source; see the additive sync request.
- Final hostrun check passed (ruff/types/architecture/contracts); the known ownership
  substep emits Not a git repository and remains unvalidated.
- Full core/contracts plus pure IPC-client tests passed with exit 0.
- GIT-2 restore tests are a separate follow-up and were not part of that full run.
- GUI/layout files and the installed application were not changed in this review.
