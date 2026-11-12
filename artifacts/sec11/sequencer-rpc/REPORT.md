# SEC-23: sequencer message/index edits through daemon RPC

Base: `4b4fe6d315510858870e74b3be38b76bb1516a92` (SEC-22).
Git 2.47.3; Python 3.15.1 free-threading build.
SEC-11/13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and boundary

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-rpc/run.py > artifacts/sec11/sequencer-rpc/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-rpc/verify.py > artifacts/sec11/sequencer-rpc/validation.json
```

The final runner executes outside the command sandbox, in the agent container,
using the existing core environment. It does not use host execution, install a
runtime, or connect to the user's running daemon. The initial sandbox attempt
stopped after migration output while connecting to the first disposable daemon;
it was interrupted. Its log is retained in sandbox-attempt.log. The cause was
not diagnosed. Retained logs have only trailing whitespace stripped. It is neither a matrix failure nor a passing RPC result.

40 disposable cases: SHA1/SHA256 × rebase/cherry-pick × observation,
prepare-commit-msg message edit, commit-msg message edit, pre-commit index edit,
prepare-commit-msg index edit × native/daemon. These are fresh native controls,
not imported SEC-22 results. Fixture/snapshot/hook helpers are reused from the
unchanged SEC-18/22 scripts. Source hashes cover 294 files: all 279 product Python
files, original SEC-11 race and host-exec/environment tests, four SEC-13 scripts,
the two census dependencies, four test helpers and both new scripts.

The daemon is the assembled Daemon.serve instance with real Unix RPC, IPC
authentication, Git services, Git subprocesses and SQLite. The existing test
fixture supplies tab ports and a fake Podman executable; no agent or terminal
is started. It is an in-process disposable daemon, not the installed live app.
Every daemon case registers its repository through project/add, starts a
conflicting operation through git/rebase or git/cherryPick, then calls
git/continue after a manual file resolution and hook-disabled native git add.
Resolution staging is explicitly outside RPC, matching the existing recovery
test workflow. This is not approval interaction or Git-stage RPC acceptance.

HOME, XDG and inherited Git/experiment configuration are isolated before daemon
imports. Identities, dates and merge rebase backend are fixed. RPC connection,
state and operation calls have deadlines; native subprocesses and hook staging
have 30-second timeouts. These deadlines do not establish descendant containment
or product timeout behavior. Snapshots disable hooks and follow trace capture.
Only each private fixture directory is normalized to CASE.

## Measured behavior

120 steps, 60 native/daemon step pairs: all outcome pairs agree. Starts conflict,
manual staging succeeds and continuations succeed. Phase traces differ in all
60 pairs. The daemon executes none of the instrumented hooks in these steps,
confirming the SEC-22 low-level observation through the service boundary.

Native prepare-commit-msg edits reach the final commit in both operations.
Native cherry-pick continuation additionally invokes pre-commit and commit-msg:
the former's staged file is committed and the latter's message edit is retained.
Native rebase continuation invokes neither phase in these fixtures; those rebase
modes are not positive controls. Cherry-pick supplies the missing controls for
both object formats. Native prepare-commit-msg index edits in both operations
survive staged and on disk, outside the committed tree. Daemon continuations
have no hook edits, messages or files because those hooks were not invoked.
This is a compatibility difference, not loss of a file written by an executed
daemon hook.

Final HEAD/target OIDs agree in 12/20 pairs, trees in 18/20, messages in 14/20,
index entries and worktree files in 14/20, status in 16/20. All observation-mode
final compared states agree. Symbolic HEAD, unmerged state and recorded recovery
metadata agree in all 20 final pairs. Independent index byte hashes are recorded
but excluded from equality because stat metadata differs across repositories.

All 20 daemon records are genuinely read from SQLite: initially no operation,
then one conflicted row after start and staging, then the same row finished after
continue, with result_oid equal to final HEAD. git/state returns the open
operation during conflict and no open operation after completion. The final
SQLite row is checked again after daemon shutdown. No restart is tested.

Each daemon creates refs/aconsole/before/<operation-id> holding the exact original
HEAD. The safety ref remains through every recorded step; no other unexpected
ref is added, and source branch refs remain unchanged. Native controls have no
AConsole safety ref, so refs are checked separately rather than declared equal
as a whole. Ignored/untracked sentinels survive all steps in all cases. Every
continuation returns to the target branch and clears active sequencer metadata.

## Verification and remaining work

verify.py recomputes matrix coverage and comparisons, validates all current
source hashes, native action/phase positive controls, message/index/tree/file
contents, safety refs, RPC results and persisted journal transitions. Final
verification PASS, repeated byte-for-byte identically. Python syntax and staged
whitespace checks pass. Evidence integrity is not hook compatibility or security
acceptance; expected compatibility differences are retained in comparison.json.

No product, policy, routing or existing security probe changed. No full core/UI,
coverage, CI/install, performance or independent security gate ran. No refusal,
abort, skip, multi-commit replay, apply backend, linked/bare recovery, daemon
restart or live UI acceptance is claimed. SEC-16/18/19 consequential refusal and
no-checkout findings still need daemon confirmation; SEC-20 helper compatibility
and SEC-21 bare publication findings remain separate work. Stage B still requires
an isolated immutable installed runtime and unchanged SEC-11/13 probes, without
shell/interpreter exceptions. Live-worktree publication remains unresolved.
