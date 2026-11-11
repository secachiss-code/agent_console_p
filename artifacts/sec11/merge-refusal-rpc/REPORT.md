# SEC-24: no-checkout merge refusal through daemon RPC

Base: `3d852af778a16c6f30b85d7093bb2caf5edb6bc8` (SEC-23).
Git 2.47.3; Python 3.15.1 free-threading build.
SEC-11/13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and boundary

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/merge-refusal-rpc/run.py > artifacts/sec11/merge-refusal-rpc/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/merge-refusal-rpc/verify.py > artifacts/sec11/merge-refusal-rpc/validation.json
```

72 fresh disposable cases: SHA1/SHA256 × merge with the installed Git fallback
sample, merge with a direct instrumented pre-merge-commit, squash × six modes ×
native/daemon. Modes observe, refuse before commit/merge, refuse in
prepare-commit-msg, fail post-success, refuse the final target's prepared
reference transaction, and fail that target's committed transaction.

The native oracle checks out target and runs merge or squash+commit; the daemon
stays on anchor and publishes target through git/mergePreview and
git/mergeWorktree. This intentional difference models SEC-16's no-checkout
comparison, not equivalent cwd/index semantics. The native control's worktree
can acquire merged files or merge state; the daemon's live anchor index bytes,
entries and worktree contents must remain unchanged. Both retain unstaged,
ignored and untracked sentinels by content hash.

Each daemon is an assembled Daemon.serve with real Unix RPC, authentication,
GitMergeService, Git subprocesses and SQLite. Existing test fixtures supply tab
ports and a fake Podman executable. No agent or terminal is started and no
connection to the user's running daemon is made. HOME/XDG and inherited Git and
experiment configuration are isolated before product/test imports. Git identities
and dates are fixed. RPC startup/calls/shutdown have deadlines; native Git
commands have 30-second timeouts. No timeout or descendant containment is tested.

The final run executes outside the command sandbox, inside the agent container;
it does not execute on the host or install software. sandbox-attempt.log records
an interrupted sandbox attempt stalled after migrations. Its cause remains
undiagnosed. development-attempt.log records an initial harness error: the RPC
preview view has no tree field. That assertion was removed before the fresh full
run; the verifier instead checks the journal's expected_tree against the native
fixture's merge-tree. Neither attempt supplies matrix acceptance. Retained logs
have only trailing whitespace stripped.

The fallback sample is copied verbatim from each initialized repository's
pre-merge-commit.sample; its SHA256 is recorded. Other hooks are executable trace
scripts. The sample's own entry is not instrumented: downstream pre-commit traces
or an explicit shell diagnostic supply the observable evidence. Reference
refusal targets exactly refs/heads/target from hook stdin, unlike SEC-16's broad
prepared refusal that could stop at ORIG_HEAD. The committed mode is a late
failure control, not an enforceable prepublication refusal.

Snapshots disable hooks and optional status locks. Traces end before observation;
only each private fixture directory is normalized to CASE. Records retain
phase/argv/stdin/cwd/GIT_DIR/index, actual response/output, refs, HEAD, target
OID/tree/message/parents/tree entries, real index entries/byte hash, file hashes,
status and MERGE_HEAD. Source hashes cover all product Python files, unchanged
SEC-11/13 probes, imported census/test helpers and both new scripts.

## Measured behavior

The installed fallback sample fails through daemon no-checkout merge with
`git-sh-setup: not found` in every mode. Native observation, post-success failure
and committed-reference failure publish successfully; native early refusals
prevent target publication. A daemon fallback failure masks the downstream
refusal policy and does not demonstrate that policy's enforcement.

With a direct pre-merge-commit, daemon no-checkout merge refuses the early hook
but publishes in prepare-commit-msg refusal and target prepared-reference refusal
modes. Squash behaves likewise through pre-commit. Both modes are real refusal
positive controls in native Git for each object format. Daemon traces lack the
prepare, reference and post-success phases in these paths. This confirms the
SEC-16 low-level compatibility difference at the service boundary; it is not a
new product regression, an approval bypass demonstration or security acceptance.

Native post-success and target committed-reference failures leave successful
publication. Recorded diagnostics and refusal events distinguish these cases
from early rejection. An error from a post hook does not imply a rolled-back ref.
The direct merge control removes the fallback failure that previously masked
later merge phases.

Each daemon case reads no journal rows before the merge request and one terminal
row afterward. git/operation returns the same response; git/state has no open
operation. The row persists unchanged after daemon shutdown, with status
finished/failed matching the RPC result and result_oid matching the published
target only on success. The source_oid, old target_oid and expected_tree remain
recorded. No intermediate started/merged journal state is sampled. No restart is
tested. These no-checkout merge cases create no safety ref: the verifier checks
the entire ref-name set, unchanged source/anchor refs and the row's old target OID.
This differs from the safety-ref behavior measured for sequencer RPC in SEC-23.

All daemon cases preserve anchor HEAD, real index bytes/entries, status and
worktree files. Successful target trees and parents match the preview candidate;
messages match the supplied message. Evidence integrity and exact comparison
counts are in validation.json and comparison.json. PASS means the saved evidence
satisfies its assertions, including the expected differences. The verifier checks
295 source hashes. Outcomes and all compared target fields agree in 22/36 pairs;
phase sequences agree in 0/36. Of 36 daemon operations, 20 finish and 16 fail.
The verifier was repeated with byte-identical output; syntax and staged
whitespace checks pass.

## Remaining work

No product, routing, policy or original security assertion changed. No full
core/UI suite, coverage, CI/install, performance, live UI, independent security
gate or installed-runtime acceptance ran. This is not permission to add helper,
shell or interpreter exceptions. SEC-18 sequencer refusal/abort/restart and SEC-19
worktree/checkpoint consequences still need daemon confirmation. SEC-20 required
helper compatibility and SEC-21 bare publication remain separate work. Stage B
still requires an isolated immutable installed runtime and unchanged SEC-11/13
probes; live-worktree publication remains unresolved.
