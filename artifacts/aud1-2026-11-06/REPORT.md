# AUD-1 — Git/data-preservation audit after WorkSession 0.1.1

Baseline: `0b7717f`. Date: 2026-11-06. Product source, existing tests and the
installed application were not changed in this audit. The probes use disposable
local Git repositories, isolated HOME/XDG, and a fake container only for session RPC.

## Reproduction and evidence

```sh
mkdir -p .aconsole-dev/aud1-replay
cp artifacts/aud1-2026-11-06/reproduce.py.txt .aconsole-dev/aud1-replay/test_audit.py
HOSTRUN_CWD=/home/sas/Projects/sp_aconsole_prototype hostrun test core .aconsole-dev/aud1-replay/test_audit.py
```

Thirteen probes completed. `observations.json` preserves the baseline results.
The raw diagnostic is stored as text, like the previous audit reproducer. Replay
it in the ignored scratch directory above; fresh observations are written there,
so replay never overwrites the committed baseline. Original execution used the
same probe as artifacts/aud1-2026-11-06/test_disposable_audit.py at baseline.
The probe intentionally RECORDS unsafe outcomes: pytest exit 0 means the audit
ran, not that its observations are all safe. Two unsafe mechanisms were reproduced;
three observations show lost bytes because one mechanism has two adapter entries.

A separate run of the existing Git/session suite passed all 74 tests:
`test_work_sessions`, `test_work_session_recovery`, `test_work_session_result_integrity`,
`test_work_session_restore_integrity`, `test_restore_boundaries`, `test_worktrees`,
`test_git_operations`, `test_git_merge`, `test_git_durability`, `test_git_rewrite`,
`test_git_history` under `tests/core`. These exercise real disposable Git repositories.

## GIT-3: repository configuration can hide dirty submodule work (high)

Paths: `infrastructure/git/history.py:GitHistory.status`,
`infrastructure/git/worktrees.py:Worktrees._status/close/_remove`.

1. Commit a local submodule and initialize it in an app-managed linked worktree.
2. Change a tracked file inside the submodule without committing it.
3. Create an ignored cache file whose discard the caller explicitly permits.
4. Set `submodule.sub.ignore=all` in the common repository config.
5. Query status and call `Worktrees.close(discard_ignored=True)`.

Observed: unstaged count is zero and the worktree is removed, including unsaved
submodule content. The same case without that config reports one unstaged change,
refuses cleanup, and preserves the bytes. No concurrent writer is needed.
The root worktree's snapshot records the gitlink, not those uncommitted submodule
bytes. Agreement to discard an ignored cache does not cover that tracked work.
Registered as plan-1 GIT-3 (node-112).

## GIT-4: ignored discard bypasses Git's dirty-file refusal (high)

Paths: `infrastructure/git/worktrees.py:Worktrees.close/_remove`,
`infrastructure/git/operations.py:GitMutations.remove_worktree`.

1. Create a clean managed worktree with an ignored cache.
2. Let application preflight report no staged/unstaged/untracked changes.
3. At the GitRunner execution boundary, create a new nonignored file immediately
   before the actual `git worktree remove` invocation.
4. Compare `discard_ignored=False` with an approved ignored discard.

Without force, both adapters refuse and preserve the new file. With ignored discard,
both use `--force`, return success and delete the nonignored file as well. The
controlled write is a deterministic ordering probe for an external writer, not a
claim that every natural scheduling interleaving was exercised.
Registered as plan-1 GIT-4 (node-113). Future work must avoid blanket force for an
ignored-only consent; it must not promise filesystem isolation from all external
writers. Rollback of failed create is a separate case to review independently.

## Preserved data and checked boundaries

| Scenario | Baseline outcome |
| --- | --- |
| Untracked binary bytes before rollback | Removed from live tree, exact bytes recoverable from safety ref. REL-22 allegation of no safety snapshot is not confirmed. |
| Dangling symlink before rollback | Removed, then restored with the exact link target from safety. REL-3 behavior is already repaired by the REL-1 unlink primitive; keep the existing node for human reconciliation. |
| Parent symlink substitution | Existing restore-boundary tests pass: outside files survive and unsafe deletion refuses. |
| Main checkout and imported worktree sessions | Closing the tab and archiving the session preserve local untracked data and Git state. |
| Bare repository | HostRepos refuses a working-tree project; HEAD remains unchanged. |
| Linked worktree with a .git file | Capture/restore succeeds without altering the main checkout's unrelated edits. |
| Unborn repository | A checkpoint captures/restores bytes without creating HEAD or the real index. |
| Leading dash | --help is staged as a literal filename; base=--help is rejected. |
| Existing foreign index.lock | Stage refuses, another owner's lock/index remain byte-identical; private checkpoint index still captures working content. |
| Conflicting merge with unrelated local edits | Merge refuses and preserves HEAD, both conflicting content and unrelated edits. |
| Session result integration/restore after mutable-ref fixes | Existing public-RPC regressions GIT-1/GIT-2 pass; original results and HEAD/index/safety are preserved. |
| Worker cleanup after later commits | Existing tests refuse cleanup, preserving newer unaccepted data. |

## Scope and continuation

The requested close/restore/force/merge/options/unusual-repository/index.lock
scenarios were executed against temporary data, and both new defects have nodes.
This is an audit result for human acceptance, not a security approval or native-CLI
handoff acceptance. GTK confirmation dialogs, arbitrary repository programs and
all possible concurrent filesystem races were not covered by these probes.

SEC-11 remains pending: the live claim is still rejected because UX-7 reserves an
overlapping file. Do not bypass that lock or mark verifying nodes accepted. Fix
GIT-3 first (deterministic data loss without a race), then GIT-4; retain the raw
baseline observations so later fixes do not rewrite the evidence.


## GIT-3 follow-up

Safety status commands now explicitly request `--ignore-submodules=none`, so
repository ignore settings cannot hide tracked or untracked submodule work.
Four config/content regressions and two public cleanup regressions reproduced the
defect before the fix. The focused state/worktree/cleanup/session suite passed 51
tests after it. GIT-4 is independent and remains recorded against the baseline.


## GIT-4 follow-up

Ordinary cleanup no longer passes `--force` for ignored discard. Git's own
working-copy check stays enabled, with command-scoped status.showUntrackedFiles=all
so repo settings cannot suppress a newly arrived untracked file. The writer also
refuses ignored data without explicit consent at its own boundary. Force used
for failed-create rollback is separate and unchanged.

Nine new regressions failed before this change: late tracked/untracked changes,
a nested repo and untracked content hidden by status config in both adapters,
plus direct writer cleanup of ignored data without consent. After the change,
54 focused cleanup/session/durability tests pass, including ignored-only removal
and runtime-lock-only cleanup. There is no automatic force fallback for nested
repositories or initialized submodules; Git may safely refuse their removal.
This keeps Git's check, not an atomic guarantee against arbitrary external writes.

Final validation after GIT-3/GIT-4: the complete core/contracts suite passed
with exit 0. Final hostrun check also exited 0 (ruff, types, architecture and
contracts). The known ownership substep still emits Not a git repository and
is not evidence of a valid ownership check. No GUI or live-user-app run occurred.
