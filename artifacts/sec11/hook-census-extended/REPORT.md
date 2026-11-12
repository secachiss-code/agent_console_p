# SEC-16: extended hook census

Base: `244abe6e2832f1f74a8404c90dcb5af01e794740` (after GIT-6).
Container experiment, Git 2.47.3; exact Python version is in results.json.
SEC-11 remains failed. SEC-14 stage A remains incomplete.

## Reproduction and evidence

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/hook-census-extended/run.py
python3 artifacts/sec11/hook-census-extended/verify.py
```

The runner created 84 disposable repositories: seven operations × six behaviors
× native/product. Operations are root commit, amend, merge, squash, fast-forward,
and merge/squash into a target without checkout. Behaviors are observation,
pre-commit refusal, prepare-commit-msg refusal, failing post-success hooks,
reference-transaction refusal in prepared, and failure in committed. Each child
Git command has a 30-second timeout. HOME, XDG and Git global/system config are
isolated. Dates are fixed. No new dependencies or host execution are involved.

Root uses public GitMutations.commit. There is no public amend operation;
amend explicitly probes GitRunner.write(commit --amend, hooks=True). Other paths
use GitMutations, including the service-shaped merge_hooks → commit_tree →
move_ref sequence for no-checkout targets. This is not daemon/RPC coverage.
The native no-checkout oracle checks out target, whereas product stays on anchor
with a temporary index. Their cwd/index roles intentionally differ.

All hooks except pre-merge-commit are generated trace scripts. pre-merge-commit
is the verbatim installed Git template, copied from each fresh repository's
pre-merge-commit.sample; its SHA256 is recorded in every case. That sample
sources git-sh-setup and execs pre-commit. Accordingly, its own entry is not
instrumented: the trace records the downstream pre-commit, not an invented
pre-merge-commit event. A failed sample can produce an empty trace and a captured
error. No absent event proves that no script entry was attempted.

results.json records phase/arguments, reference-transaction/post-rewrite stdin,
cwd/index roles and hook environment fields, actual outcomes/output, HEAD/target,
tree/message, index entries and byte hash, status, MERGE_HEAD, and worktree file
hashes before/after. Trace collection stops before final observation. The
snapshot uses status with optional locks disabled; observation cannot refresh
the index after its byte hash. Temporary repositories are removed after capture.
283 source hashes pin all product Python, both runners, original race probe and
host-exec test; the runner checks them again at completion. Raw operation output
is retained in JSON. comparison.json reports differences without relabelling
them as PASS. verify.py checks coverage and evidence consistency only.

## Measured results

All 84 cases completed. Of 42 pairs, outcomes, target trees and target messages
agree in 37 each; recorded phase/argument sequences agree in 30. The five outcome
differences are three ref_merge modes and two ref_squash modes. The 12 phase
differences are all no-checkout cases. Equal sequences do not compare every
environment field or stdin; the full records retain those fields.

| Path | Observation |
| --- | --- |
| root, amend | All six native/runner outcomes agree. pre-commit/prepare-commit-msg/prepared refusal prevents target publication; post-success and committed failures do not undo it. Amend also runs post-rewrite with argument amend. |
| merge, squash, fast-forward | All six native/product outcomes agree for each. A prepared refusal blocks before target publication; failing post-success hooks and committed phase leave the operation successful. |
| ref_merge with installed fallback sample | All six product cases fail before publication with `git-sh-setup: not found`. Native succeeds for observe/post_fail/ref_committed; the other modes refuse for their configured reason. Product refusal cases here do **not** demonstrate successful pre-commit, prepare or reference-transaction enforcement. |
| ref_squash | Product invokes only pre-commit and commit-msg. It publishes in prepare_refuse and ref_prepared, whereas native refuses. Product lacks the respective phases, as anticipated by SEC-14/15; this is not full hook compatibility. |

All 84 cases preserve ignored.txt and untracked.txt by content hash. This is a
fixture observation, not isolation from malicious hooks. All 12 no-checkout
product cases preserve live index bytes and worktree file contents. Ordinary
commit refusal need not preserve index *bytes*: Git may refresh stat/cache
metadata even when the target ref stays unchanged. The evidence keeps byte and
entry results distinct. Native pre-hook merge/squash refusal can leave prepared
merge state, while GitMutations.merge resets on non-success; that policy already
existed and is not changed here.

## Consequences and limits

The fallback sample is a concrete compatibility blocker for direct _run_hook:
the measured shell diagnostic says git-sh-setup cannot be found. Native Git's
hook setup and direct subprocess environment differ. Merely adding a helper or
interpreter exception to the ordinary Git worker is not a security fix. Keep
this fixture in the future isolated broker matrix, including linked worktrees
where the sample's GIT_DIR/hooks lookup itself needs separate measurement.

For no-checkout squash, omitting prepared/reference-transaction or
prepare-commit-msg can bypass a user's installed refusal policy relative to
native Git. This collection confirms the discrepancy at the mutation API;
it does not claim a daemon exploit or authorize a product hook expansion.
Post-success failure must remain distinguishable from failed publication in
the proposed journal/broker; native success here is measured behavior.

Stage A still needs a reviewed, resolved inventory of wrappers/dynamic argv and
other mutation/helper operations (including rebase, cherry-pick, checkout and
worktree paths), unusual repositories, and finer failure targeting. This matrix
refuses **every** prepared transaction, so merge/squash may stop at an earlier
ORIG_HEAD transaction; it does not isolate refusal of only the final target ref.
post_fail fails all installed post-success hooks together, not one at a time.
The installed fallback failure masks later ref_merge phases. No timeout,
descendant, concurrent-writer, namespace, external-canary or installed-host
security test was performed. Stage B remains gated by the broader SEC-14 matrix.

No product source, existing tests, security assertions or host policy changed.
No full core/UI/coverage/CI/package/performance run or independent gate is claimed.
Verification: runner syntax, saved evidence integrity and whitespace checks.
