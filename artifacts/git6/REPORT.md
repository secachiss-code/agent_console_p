# GIT-6: refuse changed hook index before a ref-only squash

Base: `a8cb8def8b354058058513962fe17df2761fc459`.

SEC-15 found that a pre-commit hook could stage a new file in the temporary index
used by a squash without a target checkout. The service then committed its
original preview tree, reported success, and left the hook file untracked in the
unrelated root checkout. The native squash+commit control included the new file.

## RPC confirmation and chosen behavior

Five new cases use the real daemon, SQLite journal, Git and Unix RPC. Four cases
change the candidate index (pre-commit/commit-msg × add/remove); the fifth edits
only the commit message. Before the fix: **4 failed, 1 passed / 7.287 s** in
`before.xml`. The four failures show `finished` where explicit refusal is expected.
No port result is mocked. These confirm the service path independently of the
SEC-15 direct-operation reconstruction.

The ref-only squash continues to publish the previewed tree. After hooks finish,
GitMutations now computes the tree of the temporary index through GitRunner. A
changed tree returns a failure explaining that the hook changed the candidate
index and the changes need review. An unreadable/unmerged index propagates Git's
failure. The service does not create the squash commit or move the target ref.
Message-only edits still reach the final commit.

This deliberately refuses a changed candidate rather than silently dropping it
or adding content absent from preview. The user must review the hook behavior
and prepare a new operation, for example using a checkout on the target branch.
The temporary index itself is not retained as a recoverable patch. Files written
by a hook remain in place; no automatic cleanup deletes them. This change does
not make arbitrary hook side effects transactional or undo their own writes.

Only ref-only squash is affected. The census found that ordinary native merge
also did not commit the newly staged pre-merge-commit file in its fixture, so
this fix does not impose squash behavior on merge. Native checked-out commit and
merge continue through their existing paths.

## Verification

Final focused RPC run: **5 passed / 7.829 s**, `regression.xml` and
`regression-result.json`. The four refusal cases assert unchanged target/source
refs and HEAD, byte-for-byte preservation of the real index, staged and unstaged
local work, ignored/untracked files, and preservation of the hook-created file.
They then remove the fixture hook, obtain a new preview, retry successfully and
verify the earlier journal record remains failed. The fifth checks message edits
and resulting committed files. Final tests include retry assertions added after
the initial before-fix reproduction; the failing status expectation is unchanged.

All 13 core `test_git*.py` files: **132 passed / 99.924 s**, `related.xml` and
`related-result.json`. This includes the five new cases plus existing merge,
refusal, stale-preview/CAS, journal recovery, Git host-exec/environment, history,
rewrite, cleanup, watch and RPC checks. Source hashes match the focused run.

Ruff and strict basedpyright on the modified product module pass, the latter with
0 errors, 0 warnings, 0 notes. The type configuration extends this worktree's
pyproject and points at the existing locked core virtualenv. Initial lint found
complexity/return-count issues in the inline check; it was factored into a small
private helper before final validation. No lint rule was suppressed.

Reproduction on the host with the existing environment:

```sh
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/git6/run.txt --new-only
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/git6/run.txt
```

The runner records argv, actual exit, JUnit summary, elapsed time and before/after
source hashes. All fixtures isolate HOME/config and use disposable repositories.
The original race and host-exec/environment assertions are byte-for-byte unchanged.

## Limits and attempts

Two initial container RPC attempts hung in the first test and were interrupted;
one faulthandler stack is retained in `container-attempt.txt`. It shows the
asyncio main loop waiting, not a diagnosed product regression or a test failure
at the new assertion. The exact container IPC/authentication cause was not
established. The successful before/after RPC runs use the approved host execution
path and the existing virtualenv. `after.xml` is an earlier 5-pass candidate run;
final-source evidence is `regression.xml` and the related suite result.

The new write-tree call uses the existing runner with native hooks disabled and
does not grant additional execution privileges. The known SEC-11 configuration
race and the direct host hook execution architecture remain unresolved. This is
a correctness fix, not sandbox acceptance. SEC-11, SEC-13, QA-4, PERF-7 and GATE-1
are not closed by this change. No fresh full core/UI, coverage, CI, package
installation or performance result is claimed.
