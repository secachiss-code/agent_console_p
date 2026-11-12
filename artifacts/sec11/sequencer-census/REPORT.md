# SEC-18: rebase/cherry-pick hook and recovery census

Base: `79c585efd139b34abc94e081cea6c74d6dd028eb` (after SEC-17).
Container experiment using Git 2.47.3; Python version is pinned in results.json.
SEC-11 remains failed. SEC-14 stage A remains incomplete.

## Reproduction

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-census/run.py
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/sequencer-census/verify.py > artifacts/sec11/sequencer-census/validation.json
```

The runner uses 72 disposable repositories: two operations × three flows × six
modes × native/product. Flows are clean success, conflict then resolution/stage
and continue, and conflict then abort. In conflict flows the start and staging
use observation mode; the selected failure applies only to continue/abort.
Native uses the same rebase --no-autostash, cherry-pick and continuation argv as
GitMutations. Fixtures pin rebase.backend=merge, identities and commit dates;
HOME, XDG, global/system config and inherited GIT_* variables are isolated.
Each native Git subprocess has a 30-second timeout. Product calls retain their
existing 300-second mutation timeout; no timeout or descendant test is claimed.

Hooks are generated Python trace scripts installed in the repository's own
hooks directory. Native Git is the positive control for their executability.
Modes observe, refuse pre-rebase, fail post-rewrite, fail post-commit, refuse
reference-transaction prepared for the target, and fail committed for the target.
Post-hook failures are separate modes rather than a combined failure. The
reference hook consumes and records stdin. For rebase it selects only numeric
OID updates to refs/heads/target: initial HEAD detachment and ORIG_HEAD cannot
trigger it. For cherry-pick it also accepts numeric HEAD updates while HEAD is
symbolic to target. Symbolic-ref rows, deletions and equal old/new rows are
excluded. The filter deliberately describes requested updates, not proof of a
changed branch: reset during abort can request the original OID with an all-zero
old value even when the branch already contains it. SHA-1 is explicit here;
this hook filter is not yet a SHA256 fixture or a broker policy.

Each step records actual outcome/output, hook phase/argv/stdin/environment,
refusal decision and matched target rows, HEAD and branch OIDs/trees/messages,
all refs, symbolic HEAD, index entries/bytes, unmerged entries, status, recovery
metadata contents, and worktree file hashes. Observation disables hooks and
optional status locks, and collects events before inspecting state. Only the
private case directory is normalized to CASE; metadata paths remain distinct.
Source hashes cover all 279 product Python files, the original race probe and
host-exec test, and this runner (282 files), checked again at completion.

## Observations

All 72 cases completed, recording 144 steps (72 native/product step pairs).
Outcomes agree in 65 step pairs; seven differ: clean pre-rebase refusal and all
six target-prepared refusal flows. Final target OIDs/trees agree in 31 of 36
case pairs; abort can differ in outcome while both target OIDs stay unchanged.
All six observation-mode flows have equal final states for every compared field.
Phase/argument sequences differ in all 72 step pairs because native emits hooks
and product suppresses them, including the explicit conflict-resolution staging.

GitMutations.rebase, cherry_pick and step run with hooks disabled, as reviewed
in SEC-17 (operations.py:230-237). No product case emits a hook event. Native
emits real events, including reference-transaction and post-index-change on
recovery. These API measurements do not include GitRewriteService's safety ref,
journal, approval, daemon RPC or UI state. They do not establish a daemon exploit.

Native pre-rebase refusal prevents a clean rebase from starting; product proceeds.
Pre-rebase is not called again on continuation, nor by cherry-pick. Native
post-commit failure does not undo successful rebase/cherry-pick or continuation;
post-rewrite failure likewise leaves rebase successful. Cherry-pick does not
emit post-rewrite in these fixtures. No absent phase is replaced with an invented
success or failure assertion.

Native prepared refusal reaches the final rebase branch update, preserving the
old target OID but leaving replay/recovery state. It also prevents cherry-pick
publication. On both abort paths, refusal can stop recovery after index/worktree
restoration has already happened. A failure result therefore does not imply
unchanged files/index or no remaining operation. The corresponding product
operations succeed because the installed hook is disabled. Committed failure
leaves native operations successful, including abort; publication is not rolled
back by that late failure.

Native clean cherry-pick runs prepare-commit-msg and post-commit without
pre-commit/commit-msg. Conflict continuation runs pre-commit and commit-msg too.
Native rebase uses prepare-commit-msg, post-commit, post-rewrite and pre-rebase,
plus checkout/index/reference phases; this is not equivalent to replaying every
commit hook once. The JSON retains phase order, counts and post-rewrite mappings.

Successful abort restores original HEAD, refs, symbolic branch, index entries
and all worktree file hashes in both engines. Index byte equality is recorded
separately and is not required: Git may refresh metadata. All recorded steps
preserve ignored.txt and untracked.txt by content hash, including refused steps.
This is evidence for these fixtures, not isolation from malicious hooks.

## Verification and remaining work

verify.py checks matrix completeness, current source hashes, recomputed pair
comparisons, trace refusal decisions, exact target filtering, positive refusal
and late-failure controls, ignored/untracked preservation and successful abort
restoration. validation.json records measured totals. Differences are retained
in comparison.json; evidence integrity PASS is not hook compatibility PASS.

No product code, existing tests, security assertions, host policy or runtime
routing changed. No full core/UI/coverage, CI/install, performance or independent
security gate was run. No shell/interpreter exception is authorized by this census.

Stage A still needs checkpoint/worktree traces, required-helper controls,
linked/bare/unborn/SHA256 fixtures, message/index edits during sequencer recovery,
RPC reproduction of consequential refusal differences and the no-checkout
findings from SEC-15/16. Stage B still requires the installed immutable runtime
and unchanged SEC-11/13 security probes specified in SEC-14. Publication into a
live worktree remains unresolved; this report does not close that design gate.
