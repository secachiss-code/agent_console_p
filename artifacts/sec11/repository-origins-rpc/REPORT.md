# SEC-28: linked, unborn and bare origins through daemon RPC

Base: `f01a9cb2065bfa0073d7d51b0839496fc50dd5ef` (SEC-27).
SEC-11/13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and scope

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/repository-origins-rpc/run.py > artifacts/sec11/repository-origins-rpc/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/repository-origins-rpc/verify.py > artifacts/sec11/repository-origins-rpc/validation.json
```

36 fresh disposable cases: SHA1/SHA256 × linked/unborn/bare × observation,
pre-commit refusal, reference-transaction prepared refusal × native/daemon.
Native cases reuse SEC-21's fixture and commands on fresh repositories.
Linked/unborn controls stage data.txt then commit; bare controls create a commit
object with commit-tree then publish using update-ref with an explicit old OID.
Git/Python versions and source revision are saved in results.json.

Daemon cases run the assembled Daemon.serve with authenticated UI requests over
a real Unix socket and real SQLite. Existing fixtures supply fake Podman; no
agent or terminal launches. Every case submits project/add. Accepted projects
use git/state freshness values for git/stage and git/commit. No direct product
mutation stands in for an unavailable RPC. HOME/XDG, global/system Git config,
inherited GIT_* settings, identity and dates are isolated or pinned before daemon
fixture imports. Hooks live in the common Git directory and record actual phase,
arguments, reference stdin, cwd, selected Git environment and refusal.

The sandboxed attempt stalled after migrations and ended with timeout exit 124;
sandbox-attempt.log is retained without an acceptance claim. Completed runs were
explicitly approved outside the command sandbox inside the agent container.
They do not execute on the host or install a runtime. Console log trailing
padding is trimmed; JSON evidence is unchanged.

## Measured results

Linked and unborn repositories are accepted on both formats. All 24 available
native/daemon step pairs agree in outcome. HEAD/tree/message, refs, index entries
and contents, files and metadata roles agree. Raw index SHA256 differs between
independent fixtures because it includes filesystem stat metadata; equal entries
and contents are checked separately. Stage succeeds with the updated bytes:
native emits post-index-change, daemon stage emits no hook. Both commit paths
emit post-index-change before pre-commit and the same subsequent hook phases.

Successful commits execute pre-commit, prepare-commit-msg, commit-msg and
post-commit once, with prepared/committed reference events. Pre-commit refusal
stops before message/reference hooks. Prepared reference refusal produces
prepared/aborted. Both refusal modes preserve HEAD and refs and keep the staged
updated data and files available. This does not assert raw index bytes stay
unchanged during a failed commit or roll back newly written Git objects.

Successful commits also emit a separate AUTO_MERGE cleanup reference sequence:
aborted, prepared, committed with all-zero old/new OIDs. These are preserved and
checked separately from the HEAD/branch publication transaction. An intermediate
verifier rejected those additional events; the final verifier accounts for their
exact phases and stdin rather than assuming one transaction per commit.

Linked calls originate in the linked checkout; common and per-worktree metadata
remain distinct. The main checkout's target branch stays at its original OID.
Unborn fixtures start without a HEAD commit; successful commit creates it and
refusal keeps it unborn. Ignored/untracked sentinels survive every operation.
No hook edits index entries or the commit message in this matrix.

All six bare project/add requests return PROJECT_NOT_A_GIT_REPO (code 2002),
create no project record, execute no hook and preserve the recorded repository
state. Native bare commit-tree creates a candidate object without these hooks;
update-ref executes reference prepared/committed, or prepared/aborted on refusal.
Native pre-commit refusal mode does not block plumbing update-ref because that
hook is absent. Bare indexes stay absent. The SEC-21 low-level move_ref finding
is therefore **not reproduced through this public project path**. The current
GitService method registry has no standalone commit-tree/update-ref endpoint;
no invented endpoint or direct GitMutations call substitutes for one. This is
not a claim about every service operation using a non-checked-out target ref.

All queries of git_operations before/after operations and after graceful stop
return zero rows. These stage/commit operations do not use that journal. SQLite
persists one project for each accepted case and no project for bare cases.
Repository snapshots and database rows after stop equal their pre-stop values;
discovery also preserves recorded repository state. This is graceful-stop
observation, not restart/crash recovery acceptance. Safety refs are not created.

## Validation and remaining work

verify.py checks matrix coverage, source hashes, recomputed parity, native
positive/refusal controls, reference phases/targets, OID widths, file contents,
metadata roles, bare rejection, RPC results and SQLite evidence. Integrity PASS
does not mean compatibility or security acceptance. All 294 source hashes cover
product Python, reused fixtures, original SEC-11 probes, SEC-13 artifacts and
runner/verifier; they match at runner completion and verification. Repeated
verification is byte-identical; Python syntax and staged whitespace checks pass.

No product, tests, policy, runtime routing or security assertions changed. This
matrix excludes alternate hooksPath, worktree-specific config, hostile gitdir/
commondir/alternates, configuration races, helpers, signing, hook edits, real
Podman, installed runtime, live UI, approvals, full core/UI, coverage, CI/install,
performance and independent host-boundary acceptance.

Next: reconcile the stage A gate in DESIGN.md against the accumulated command,
hook and RPC evidence, naming concrete uncovered phases and compatibility
decisions. Required-helper behavior still silently stages unfiltered content;
standalone checkpoint capture/delete lack public RPC acceptance. Stage B needs
an isolated immutable installed runtime with unchanged SEC-11/13 probes and no
shell/interpreter exceptions. Live-worktree publication remains unresolved.
SEC-28 closes this evidence task only, not SEC-11/13 or stage A.
