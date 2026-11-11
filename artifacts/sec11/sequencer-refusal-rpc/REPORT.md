# SEC-25: sequencer refusals, abort and graceful restart through daemon RPC

Base: `801f9cc36131be5e3abcc52e4e341c82e6b778be` (SEC-24).
Git 2.47.3; Python 3.15.1 free-threading build.
SEC-11/13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and boundary

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-refusal-rpc/run.py > artifacts/sec11/sequencer-refusal-rpc/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-refusal-rpc/verify.py > artifacts/sec11/sequencer-refusal-rpc/validation.json
```

The final runner executes outside the command sandbox, inside the agent
container, using the existing core environment. It does not execute on the
host, install a runtime or connect to the user's daemon. The sandbox attempt
stopped after migrations while entering its first disposable daemon; it was
interrupted and its log retained. That attempt is not a matrix result. The
underlying stall was not diagnosed. Retained logs have trailing whitespace
stripped only.

144 fresh disposable cases: SHA1/SHA256 × rebase/cherry-pick × clean success,
conflict then continue, conflict then abort × observation, pre-rebase refusal,
post-rewrite failure, post-commit failure, final-target reference-transaction
prepared refusal, final-target committed failure × native/daemon. Native
controls execute afresh; no historical case results are imported.

The unchanged SEC-18 fixture/snapshot/native-action helpers are reused. Their
hook template is extended in memory to select numeric 40- or 64-character OIDs;
the original source remains unchanged. Reference hook stdin selects target
branch publication (also symbolic HEAD updates for cherry-pick), excluding
ORIG_HEAD, initial HEAD detachment, symbolic-ref rows, deletions and equal OIDs.
The verifier recomputes the selection from recorded stdin and HEAD. An abort
may request original-OID publication even when the target ref already has that
OID; a selected row is a requested transaction, not proof of a branch change.

The daemon is a real assembled Daemon.serve with Unix RPC authentication, Git
services/subprocesses and SQLite. Existing tests supply tab ports and fake
Podman, without starting agents or terminals. Every daemon case registers a
project with project/add and invokes git/rebase or git/cherryPick. Conflicts
are resolved and staged outside RPC with hooks disabled, then git/continue or
git/abort is called. Native resolution staging uses SEC-18's native git add;
that staging-phase difference is retained, not attributed to daemon Git-stage
RPC. No approval interaction or installed live UI is tested.

A restart closes the client, requests daemon shutdown, awaits successful
Daemon.serve exit, constructs a distinct daemon instance on the same database,
connects and authenticates a new RPC client, and reads git/state. Every conflict
flow restarts before continuation/abort; every case restarts after its terminal
result. This tests graceful lifecycle and persisted state, not process crash,
power loss, mid-command interruption, or resumption of an in-flight worker.
Native controls have no daemon lifecycle.

HOME, XDG and inherited Git/experiment settings are isolated before daemon
imports. Git identities/dates and rebase merge backend are fixed. Subprocesses,
RPC and lifecycle waits have deadlines; these do not establish product timeout
or descendant containment guarantees. Snapshots disable hooks and are captured
after event collection. Only each private case directory is normalized to CASE.
Refs are checked separately because daemon operations add their own safety ref.

## Measured behavior

All 144 cases completed, recording 288 action steps and 144 native/daemon step
pairs. Outcomes agree in 130 pairs; the 14 differences are clean pre-rebase
refusal (one per object format) and final-target prepared refusal for both
operations and all three flows (six per format). Phase/argument sequences
differ in every step pair. No instrumented hook executes in a daemon action.
The daemon reproduces SEC-18's low-level compatibility differences through
the service boundary; this does not demonstrate a security bypass.

Native pre-rebase refusal stops clean rebase before publication. Native
final-target prepared refusal blocks rebase/cherry-pick publication and can
leave active recovery metadata. Prepared refusal on either abort path fails
even after index/worktree restoration. Successful abort controls restore
original HEAD, target, symbolic branch, index entries and all worktree files.
Post-commit/post-rewrite/committed failures leave native operations successful
when those phases actually execute. Pre-rebase is absent on continuation and
cherry-pick; post-rewrite is absent on cherry-pick. The verifier requires real
positive controls for every injection mode in each object format without
claiming that an absent phase was tested in every flow.

Final target OIDs/trees agree in 62/72 case pairs; final HEAD OIDs/trees in
64/72, messages in 66/72, index entries/files in 70/72, symbolic HEAD/status in
66/72, and recovery metadata in 58/72. Unmerged entries agree in all final
pairs. Every observation-mode final compared state agrees. Index byte hashes
are recorded but excluded from cross-repository equality because stat data
differs. Ignored and untracked sentinels survive every action in every case.

All 72 daemon cases have one real persisted SQLite journal row. There are 48
finished and 24 aborted terminal rows, with no open operation after completion;
finished result_oid matches final HEAD. Conflict remains the same conflicted
row until continue/abort; RPC reports its exact id. Each daemon case retains
the safety ref refs/aconsole/before/<id> at the original HEAD. It is the only
additional ref; source refs remain unchanged.

All 120 graceful restarts pass: 48 in conflict and 72 after terminal completion.
The full recorded Git snapshot, including index byte hash, is identical before
and after each restart. SQLite rows are identical before stop, after stop and
after the next daemon starts; RPC operation records also agree. Conflict flows
successfully continue or abort through the new daemon/client. Hooks emit no
events during restart. This establishes persistence and recoverability at the
tested idle boundaries; it says nothing about an abrupt crash during mutation.

## Verification and remaining work

verify.py recomputes coverage, comparisons, current source hashes, native
positive refusal/failure controls, hook selection from stdin, sentinels,
successful abort restoration, safety refs, RPC/journal transitions and state
across new daemon instances. Evidence integrity PASS is distinct from hook
compatibility and security acceptance; expected differences remain in the data.
All 293 source hashes, including original SEC-11/13 probes and fixture helpers,
remain unchanged. Final verification PASS was repeated byte-for-byte; Python
syntax and staged whitespace checks pass.

No product, policy, routing, existing tests or original security probe changed.
No full core/UI, coverage, CI/install, performance or independent security gate
ran. There is no skip, multi-commit replay, apply backend, linked/bare recovery,
hook message/index edit acceptance, crash recovery or malicious-hook isolation
claim here. SEC-23 covers a separate RPC edit matrix; SEC-24 covers no-checkout
merge/squash refusals. Worktree/checkpoint SEC-19, required-helper SEC-20 and
repository-origin SEC-21 findings still require their own daemon acceptance.
Stage B still requires an isolated immutable installed runtime and unchanged
SEC-11/13 probes, without shell/interpreter exceptions. Publication into a live
worktree remains unresolved. This census does not close that design gate.
