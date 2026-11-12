# SEC-22: message and index edits during sequencer recovery

Base: `150f9bb4d427624ae0179fca08f644799ff3b539` (after SEC-21).
Container experiment with Git 2.47.3; results.json records the Python version.
SEC-11 and SEC-13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and scope

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-edits/run.py > artifacts/sec11/sequencer-edits/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-edits/verify.py > artifacts/sec11/sequencer-edits/validation.json
```

40 disposable cases: SHA1/SHA256 × rebase/cherry-pick × five modes ×
native/product. Each case starts with a real conflict, resolves shared.txt,
stages it, then continues. Hooks only observe during start/staging. Continuation
modes are observation, message prefix from prepare-commit-msg or commit-msg,
and adding/staging hook-added.txt from pre-commit or prepare-commit-msg.
Each mode changes one phase and one kind of artifact. The hook's staging
subprocess disables hooks to avoid recursive trace events and retains its
inherited Git environment/index. No refusal, abort, skip, multi-commit replay,
apply backend, linked worktree or bare recovery is claimed here.

The runner imports the unchanged SEC-18 fixture, snapshot and comparison helpers;
the dependency is included in source hashes. Fixtures use rebase.backend=merge,
fixed identities/dates and isolated HOME/XDG/global/system config, clearing
inherited GIT_*, SEQ_* and EDIT_* variables. Native subprocesses and the hook's
staging subprocess have 30-second timeouts. Product mutation timeouts remain
unchanged (300 seconds). This is not timeout or descendant containment testing.

Every step records output/outcome, hook argv/stdin/environment and actions,
message before/after, HEAD/target OIDs, trees, messages, refs, symbolic HEAD,
index entries/bytes, unmerged entries, status, recovery metadata and worktree
hashes. HEAD tree entries and hook-file blobs from HEAD/index are recorded
separately. Observation disables hooks; trace is captured before observation.
Only the fixture directory is normalized to CASE. Source hashes cover 279
product Python files, the original race probe and host-exec test, four SEC-13
Python artifacts, the SEC-18 runner and both new scripts: 288 files, checked
again after execution. Independent index byte hashes are recorded but excluded
from native/product equality because fixture file stat metadata can differ.

## Measured behavior

All 40 cases complete, producing 120 steps / 60 native-product step pairs.
All 60 outcome pairs agree: start conflicts, resolution staging succeeds,
continuation succeeds. Phase sequences differ in all 60 pairs; product
GitMutations suppresses hooks throughout these operations. Native hook scripts
execute and record their actual edits, providing positive controls.

Native continuation of both operations invokes prepare-commit-msg exactly once;
its message prefix reaches the final commit. Cherry-pick continuation also
invokes pre-commit and commit-msg exactly once; the commit-msg edit reaches its
commit. Rebase continuation invokes neither pre-commit nor commit-msg in these
fixtures. Their rebase edit modes therefore have no action, rather than a
successful positive control. Cherry-pick supplies the positive controls for
both phases in each object format. Rebase invokes post-rewrite; cherry-pick
does not. Both invoke post-commit once. Full index/reference phases and stdin
remain in the raw traces.

Native pre-commit staging during cherry-pick is present in the committed tree,
index and worktree. Native prepare-commit-msg staging during **both** operations
is absent from the committed tree but remains staged in the index and present
in the worktree after success (`A  hook-added.txt`). The file is preserved.
This late index edit must not be interpreted as a committed edit or silently
discarded by a future adapter. The initial verifier assumed any staged hook
edit entered the commit; measured native results disproved that assumption.
The final verifier explicitly checks the separate committed/staged outcomes.

Product continuation emits no hooks, no message prefix and no hook-added file.
Consequently final HEAD/target/refs agree in 12/20 case pairs, final trees in
18/20, messages in 14/20, index entries and files in 14/20, status in 16/20.
All observation-mode final compared states agree. Recovery metadata, symbolic
HEAD and unmerged state agree in all 20 pairs. Both object formats show the
same phase/edit behavior. Ignored and untracked sentinel contents survive
every step; all continuations return to target and clear active recovery state.

## Verification and next work

verify.py independently checks matrix completeness, current source hashes,
recomputed comparisons, conflict/stage/continue states, OID lengths, actual
phase counts/actions, positive controls in each format, message propagation,
separate HEAD/index/file contents and sentinel preservation. A repeated verifier
must produce identical validation.json. Evidence integrity PASS is not Git hook
compatibility or security acceptance.

This is low-level GitMutations evidence, excluding GitRewriteService approval,
safety refs, service journal, daemon RPC and UI. It establishes no daemon exploit.
Consequential refusal and hook-edit differences still require daemon/service
confirmation, including prior SEC-16/18/19 refusal/no-checkout and SEC-20/21
helper/bare publication findings. Stage A is not declared complete. Stage B
requires an isolated installed immutable runtime and unchanged SEC-11/13 probes;
live-worktree publication and required-helper compatibility remain unresolved.

No product source, policy, routing or existing probe changed. No full core/UI,
coverage, CI/install, performance or independent security gate was run. No
shell/interpreter exception is authorized by this experiment.
