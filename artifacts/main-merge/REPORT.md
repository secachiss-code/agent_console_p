# Integrate local work and publish main

Requested by the user: push to main and inspect all Git branches. Base agent tip
211c1104; local main 0885aff6; fetched origin/main 9ffd7dbd. The user explicitly
authorizes merging and pushing, overriding the old checkout AGENTS prohibition.
No force-push, branch deletion or history rewriting is used.

The merge preserves local main approval policy, screen state and IPC transport
changes, and agent-branch localization, palette, settings drafts and UI fixes.
Conflicts in translated messages are resolved by retaining the shared gettext
catalog and adding all missing main message IDs in both en/ru catalogs.

Both branches independently added revision 0008 after 0007. Added a no-op Alembic
merge revision 0009_merge_approval_fork with both 0008 parents; neither historical
migration is rewritten. The initial GUI run before this correction timed out
at 360 seconds after multiple real-core startup failures; it is not a PASS.

## Branch audit

All six ac/* agent branches, dev/core, dev/front and both origin/ac/* refs are
contained in the combined merge-parent history. All other existing worktrees
were clean when inspected. No worktree was deleted or modified during the audit.

Three backup/* branches have old commits outside this history (74/91/91);
they are retained. docs/0.1.1-project-docs has four different commit IDs: three
are patch-equivalent to main, and the APR-4 commit differs only by the IPC
snapshot/transport context into which it was applied (git range-diff checked).
Its work is already present in main. The documentation branch is retained.
Raw refs/counts and worktree state are in branches-before.json/worktrees-before.json.

## Validation

Ruff src/tests/migrations passes; strict basedpyright passes after correcting
ParamSpec namespace and typing the empty capability set inherited from main.
Final targeted tests with explicit PYTHONPATH=src and native IPC permission:
**55 core tests passed** in 40.28s, including both migration upgrade paths,
approval policy, fake method coverage, legacy history adoption, Git cleanup,
durability and subscriber updates. GUI repeat: **9 passed** in 3.61s.

Full UI after the migration fix: 448 passed / 3 failed. Failures were PERF-7
and two GUI waits; both GUI scenarios passed in the separate repeat. The full
UI and broad core runs overlapped, so these are not controlled performance
comparisons. No threshold/assertion was relaxed.

The initial broad core run (1520 passed / 8 failed) resolved editable imports
from the main checkout instead of this worktree. It is preserved as
core-import-mismatch.log and is not accepted as validation of the merged tree.
Subsequent runs explicitly set PYTHONPATH=src. The final corrected targeted run
is core-verified.log; the complete core suite has not been repeated on the final
tree. Initial sandbox IPC runs could not complete and are not PASS results.

Legacy migration fixtures now insert through reflected historical table columns,
with their original branch/time values, rather than using the newest Project ORM
against the 0004 schema. The fake core now covers project/setApprovalPolicy, and
the README retains the Codex hook-trust instructions from main.

Six native UI snapshots (tabs/preferences, light/dark/narrow) were opened and
reviewed. Visible labels are translated, dark dialogs have no light patches,
and the narrow sidebar overlays the main area as designed. Preference groups
below the visible scrolled area are not covered by these screenshots. The
referenced docs/design/DESIGN.md is absent, limiting comparison with that canon.

Whitespace checks pass excluding the inherited Cursor terminal .bin capture,
whose CRLF/control bytes are intentionally preserved. No fixture bytes changed.
Final remote verification is performed after committing and pushing.

PERF-7 remains open: the last pre-merge complete UI run had 443 passed and one
known frame-time failure, median65ms vs17.5ms. This integration does not relax
performance assertions or claim that the performance issue is fixed.

Three stale detached worktree registrations were observed and retained; no prune
or branch deletion was requested or performed.
