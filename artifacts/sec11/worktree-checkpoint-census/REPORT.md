# SEC-19: worktree and checkpoint hook census

Base: `c600e18754a750ef6a6b15337db876c0da75eac2` (after SEC-18).
Container experiment, Git 2.47.3; full Python version recorded in results.json.
SEC-11 and SEC-13 remain failed. SEC-14 stage A remains incomplete.

## Reproduction and scope

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/worktree-checkpoint-census/run.py > artifacts/sec11/worktree-checkpoint-census/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/worktree-checkpoint-census/verify.py > artifacts/sec11/worktree-checkpoint-census/validation.json
```

80 disposable repositories: SHA-1/SHA-256 × five operations × four hook modes ×
native/product. Operations are Worktrees.create, Worktrees.create_detached,
GitCheckpoints.capture, materialize and delete. Modes observe, refuse the selected
reference transaction at prepared, fail it at committed, and fail post-checkout.
The new worktrees are real linked worktrees. The caller repository is an ordinary
populated checkout; calls originating from linked, bare and unborn repositories
are still outside this matrix. This does not cover closing/pruning a worktree.

Native uses direct Git commands corresponding to the APIs: worktree add -b;
update-ref plus worktree add --detach; and the checkpoint private-index plumbing
read-tree/add/update-index/write-tree/commit-tree/update-ref. It is deliberately
not git stash or an ordinary commit. Native restore follows the same sorted
per-file restore calls, then removes extra ordinary flat fixture files. No
symlink or path-deletion boundary claim is made. No .worktreeinclude or setup
script is installed. Native does not synthesize AConsole's lock or ownership
records. Those application artifacts are recorded separately.

For materialize/delete, both engines start from an identical checkpoint created
through GitCheckpoints before hooks are installed; only the requested operation
is traced. Capture starts with different staged/worktree contents plus untracked,
ignored and runtime-lock files. HOME/XDG/global/system Git configuration and
inherited GIT_* variables are isolated. Commit dates/identities are pinned. Each
native command has a 30-second timeout; product retains its existing timeout.
No timeout, cancellation or descendant-containment test is claimed.

Hooks are executable Python trace scripts in the common repository hooks folder.
Native execution is their positive control. Trace records contain phase, argv,
stdin, cwd, GIT_DIR, private-index presence and the refusal decision. Reference
selection uses only these exact fixture refs: refs/heads/ac/census-tab,
refs/aconsole/executors/census/base and refs/aconsole/checkpoints/census/1.
It deliberately includes equal all-zero old/new rows. During exploratory checking
an old!=new predicate missed actual deletions; it was removed before the saved
final run. Here update-ref -d without an expected old OID emits all-zero rows
even while deleting an existing ref, in both object formats. This filter is
fixture instrumentation, not a proposed authorization policy or proof of a
particular publication state.

Observations use hook-disabled Git and capture hook events before inspection.
State includes HEAD/symbolic branch, all refs, checkpoint tree entries, real
index entries and byte hash, source/destination file hashes, worktree registration,
runtime lock and AConsole ownership records. Only private fixture paths and hook
private-index filenames are normalized. Source hashes cover 279 product Python
files, the original race probe, host-exec test, all four SEC-13 Python artifacts,
and the runner (286 files), checked again at completion.

## Measured results

Outcomes agree in 26/40 pairs. Native phases are present in all cases and product
emits no hooks. Four prepared refusals (branch creation, detached safety ref,
checkpoint publication and checkpoint deletion), plus three post-checkout failures
(two worktree creation paths and materialize), differ per object format.
All eight committed-failure cases report successful mutation despite the failing
native hook. A phase absent from an operation is not counted as a refusal control:
materialize does not update refs; capture/delete do not run post-checkout.

Native prepared refusal preserves the original refs and prevents worktree
creation/checkpoint publication/deletion. The corresponding product API succeeds
because its GitRunner calls disable hooks. This matches the current runner
contract, not a newly introduced regression or evidence of a daemon exploit.
Native refused capture has already written objects before update-ref fails;
this experiment does not census unreachable objects or treat ref refusal as
rollback of the object database.

Native post-checkout failure returns an error after worktree creation. Its files,
branch/safety ref and Git registration remain. Native materialize restores
tracked.txt first, its post-checkout fails, and untracked.txt plus extra.txt stay
in their later state. Product completes both restores and removes extra.txt.
An error therefore does not imply an untouched worktree. Worktree post-checkout
uses the branch-change flag 1; per-file restore uses flag 0. The complete argument
and phase order is retained rather than reducing all checkout hooks to one call.

Native capture emits post-index-change for the private index and reference hooks
for publication, without ordinary commit hooks. Restore emits post-checkout once
per restored file on success. Delete emits reference hooks, including an aborted
phase before prepared in these fixtures. These operations cannot be represented
by one pre-commit/post-commit pair in a future broker.

HEAD, symbolic branch, real index entries **and real index bytes** remain unchanged
within every case. Ignored-file content and the source runtime lock also survive
all cases. Capture excludes ignored.txt and .aconsole/lock from its tree and
includes both tracked worktree content and untracked.txt. Successful materialize
restores both snapshot files and removes extra.txt, preserving the staged index.
The verifier checks these content assertions. Observation-mode pairs agree on
refs, checkpoint tree, index entries and source/destination ordinary files.

Cross-repository index byte hashes differ because fixtures have separate file
metadata; that comparison is recorded without treating it as a content defect.
Product destination locks and ownership records also intentionally differ from
plain native Git. All such comparisons remain visible in comparison.json.

## Validation and limits

verify.py checks matrix completeness, 286 source hashes, recomputed comparisons,
hook targeting including zero-row deletions, 40/64-character OIDs, positive
refusal/late-failure controls, partial restore, content preservation, private index
use, excluded runtime files and product ownership artifacts. validation.json's
evidence_integrity PASS means these assertions hold; it is not compatibility or
security acceptance. Repeated verification produced identical output.

Product sources, existing tests/security assertions, host policy and routing did
not change. No full core/UI/coverage, CI/install, performance or independent host
security gate was run. These are direct Worktrees/GitCheckpoints API observations,
not daemon RPC, service journal, approval, installed runtime or UI acceptance.
No shell/interpreter exception or helper execution is authorized by this census.

Stage A still needs required-filter/custom-merge-helper positive controls,
operations originating from linked/bare/unborn repositories, sequencer message
and index edits during recovery, and daemon reproduction of consequential
refusal/no-checkout differences from SEC-15/16/18/19. SHA256 coverage here does not
extend the earlier sequencer matrix. Stage B still requires the isolated immutable
runtime and unchanged SEC-11/13 attack probes. Live-worktree publication remains
an unresolved design gate.
