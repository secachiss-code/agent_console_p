# SEC-15: native/product hook compatibility census

Base: `bd9e3d94ced8a78943b88d48e3f5be754ccc562a`.
Scope: explicit experiment in the agent container, Git 2.47.3 and the existing
locked core Python 3.15.1 environment. **SEC-11 remains failed.**

The census completes a bounded compatibility investigation. It does not complete
the entire stage-A command/phase census in SEC-14, implement a sandbox or certify
hooks as safe. Product source and existing security assertions are unchanged.

## Reproduction and method

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/hook-census/run.py
```

The explicit runner makes 48 disposable repositories: six operations, four hook
behaviors, and native/product pairs. It installs eight trace hooks only after
preparing each repository. HOME, XDG config and Git global/system configuration
are isolated. No dependencies are installed, host namespace is entered, service
is restarted or existing repository mutated. Git child processes have timeouts.

Operations: commit, merge commit, squash+commit, fast-forward, and merge/squash
into a target without a checkout. Behaviors: observation, pre-hook refusal,
commit-msg message editing, and a pre-hook that creates and stages a file.

Native runs use ordinary Git. Product runs use public `GitMutations` methods.
For targets without a checkout, the runner explicitly follows the sequence in
`GitMergeService._run`: merge_hooks, commit_tree and old-OID move_ref. This is
not a daemon/RPC test. The native oracle checks out the target; the product runs
from an unrelated anchor branch with a throwaway candidate index, matching the
existing implementation. Different live cwd/index contents are intentional and
must not be described as identical environments.

Each case records phase order/count, arguments, cwd role, index role, editor,
exit outcome, target OID/tree/message/files, index entries and porcelain status.
Trace capture ends before the final observation queries. No timing or ordering
claim beyond the saved serial invocation is made. Object IDs may differ across
cases due to commit timestamps; content comparison uses tree IDs and messages.

`results.json` pins all 279 product Python files, the unchanged original race and
host-exec test, and the runner (282 hashes). The runner verifies these bytes are
unchanged at completion. `comparison.json` is generated from the recorded cases
and reports differences directly; expected differences do not become fake PASS
assertions. Exit zero means collection completed, not security acceptance.

## Results

All 48 cases completed. Across the 24 pairs: outcomes agree in 24, final tree
contents in 23, messages in 24, and recorded phase/argument sequences in 16.
The eight sequence differences are the two no-checkout operations across all
four behaviors. They are a compatibility boundary, not automatically eight bugs.

| Operation | Main hook sequence observed | Notable result |
| --- | --- | --- |
| commit | pre-commit, prepare-commit-msg, commit-msg, post-commit | Native/product agree; staged hook file is committed |
| merge | pre-merge-commit, prepare-commit-msg, commit-msg, post-merge | Native/product agree; a newly staged hook file is not in the committed tree in either case |
| squash+commit | post-merge, pre-commit, prepare-commit-msg, commit-msg, post-commit | Native/product agree; staged hook file is committed |
| fast-forward | post-merge | Refusal in pre-commit/pre-merge-commit is never reached; both succeed |
| no-checkout merge | product: pre-merge-commit, commit-msg | prepare-commit-msg and post-merge are absent from product trace |
| no-checkout squash | product: pre-commit, commit-msg | prepare-commit-msg/post-commit absent; staged hook file missing from product commit |

The abbreviated table omits reference-transaction and post-index-change events;
full JSON preserves them, including prepared/committed/aborted arguments and
repeated occurrences. These phases cannot simply be forgotten when designing a
broker. No-checkout product calls normally suppress them through GitRunner, but
the staging hook's own nested Git add does run post-index-change. That nested
command is currently executed by the hook outside GitRunner. The proposed broker
must contain the whole hook process tree, not merely its entrypoint.

Pre-hook refusal preserves target OID and index in all ten product refusal cases
that actually reach the pre-hook. Native merge/squash refusal leaves prepared
index state; the product wrapper resets it. Fast-forward does not run those
pre-hooks, so the two fast-forward cases labelled refuse are not refusal coverage.
Commit-msg edits reach the commit for all five operations that invoke commit-msg;
fast-forward makes no commit and does not invoke it.

## Confirmed no-checkout squash discrepancy

In `ref_squash/stage`, pre-commit creates `hook-added.txt` and runs Git add using
the supplied temporary index. Both engines report success. Native squash+commit
includes that file; product commits the original candidate tree, leaves the hook
file untracked in the anchor worktree, and does not include it in the commit.
The pair has different final tree IDs. Message edits in the same no-checkout path
do propagate correctly.

Follow-up plan node: GIT-6 (node-387), starting with an RPC regression.

The source explains the observed result: `merge_hooks` discards the temporary
index after hooks and returns only message text. `GitMergeService._run` passes
the original candidate tree to commit_tree. The differential experiment does
not establish how often this occurs in user projects. It needs a fresh RPC
regression before changing product behavior, including a decision about intended
hook index mutations versus preserving the reviewed preview tree. Silently
discarding a mutation and reporting full hook compatibility is not acceptable.

Do not generalize the finding to pre-merge-commit: the native merge control also
omits the newly staged file from the commit in this fixture. Do not patch every
operation to consume the temporary index based on the squash observation alone.

## Inventory and remaining work

The AST inventory contains 51 candidate read/write/invoke/direct-hook call sites
with file, line, receiver and argument expressions. It is a syntactic inventory,
not whole-program receiver resolution: wrappers, dynamically built arguments,
indirect subprocesses and new receiver names require manual review. It does not
prove a complete command allowlist. Do not use it alone to enable stage B.

Missing cases include root/amend commits, pre-hook fallback scripts, failing
post-hooks, reference-transaction refusal, unusual repositories, symlink/rename
attacks, external concurrent writers, timeouts/process descendants, environment
injection and installed host containment. Stage A remains broader than this
census. Existing SEC-11 and SEC-13 probes remain required for stage B unchanged.

Final verification: source hash consistency, JSON result/comparison consistency,
48-case completeness, runner syntax, and diff whitespace checks. No full core,
UI, coverage, CI, package install, performance or independent security gate was
run or claimed for these diagnostic-only additions.

The initial result-integrity check used the system Python and could not import
msgspec. It was repeated successfully with the same locked core environment used
for collection; this is an environment mistake, not a collected-case failure.
