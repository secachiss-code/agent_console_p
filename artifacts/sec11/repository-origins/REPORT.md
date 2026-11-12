# SEC-21: linked, unborn and bare callers

Base: `4ea6c1b852d0a4f72271a76e1513c30f0b2e9c88` (after SEC-20).
Container experiment with Git 2.47.3; exact Python version in results.json.
SEC-11 and SEC-13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/repository-origins/run.py > artifacts/sec11/repository-origins/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/repository-origins/verify.py > artifacts/sec11/repository-origins/validation.json
```

36 disposable cases: SHA-1/SHA-256 × linked/unborn/bare origins × observation,
pre-commit refusal, reference-transaction prepared refusal × native/product.
Linked and unborn cases perform stage then commit. Bare cases create a commit
object using commit-tree, then publish with update-ref and an explicit old OID.
Product calls are GitMutations.stage/commit/commit_tree/move_ref. Native calls
use matching arguments. Bare cases have an initial commit with a real empty tree;
they do not model a full GitMergeService operation or a bare merge oracle.

HOME/XDG/global/system config and inherited GIT_* variables are isolated; identities
and dates are pinned. Linked fixtures install executable hooks in the common
repository's hooks directory and originate both calls from the linked checkout.
Unborn fixtures have no HEAD commit until the measured commit. Hooks record real
phase, arguments, reference stdin, cwd, selected Git environment and refusal.
No configured alternate hooksPath, worktree-specific config, hostile metadata,
alternates, config races or helper permissions are tested here.

## Measured observations

72 steps produce 36 native/product step pairs. Outcomes agree in 34/36 pairs.
The two differences are bare update-ref prepared refusal on SHA-1 and SHA-256:
native returns failure, preserves the old ref and records prepared/aborted;
product succeeds and publishes the candidate without invoking hooks.
Product move_ref uses the existing hooks-disabled runner path. This extends the
earlier ref-update observations to actual bare callers; it is not a new daemon
exploit or proof that these low-level calls are exposed by an RPC.

Linked/unborn stage records post-index-change under native Git and no hook under
product. Both stage the updated bytes. Commit in both engines records
post-index-change before pre-commit. Successful commit invokes pre-commit,
prepare-commit-msg, commit-msg and post-commit exactly once, along with the saved
reference-transaction events. Both engines honor the refusing pre-commit and
prepared reference hook. A refusal preserves HEAD and refs and leaves the staged
index entries and files available; failure does not imply unchanged raw index
bytes. No hook edits the index/message in this experiment.

Bare commit-tree invokes none of these hooks in either engine and creates the
same candidate OID without publishing it. Bare native update-ref emits prepared
and committed when successful, including in pre-commit refusal mode: pre-commit
is absent for this plumbing command. Product emits no hook. The bare index is
absent throughout. Successful update-ref changes HEAD's resolved OID/message but
retains the empty tree. Refusal still leaves the already created commit object;
there is no object-database rollback claim.

Outside the bare refusal difference, paired snapshots agree in all fields except
raw index SHA-256. Index files contain filesystem stat metadata from independently
created fixture files. Entry OIDs/content, HEAD/tree/message, refs, files, common
directory and per-worktree metadata roles are compared separately. Linked writes
preserve the main checkout branch. Ignored/untracked file contents survive all
linked/unborn steps. Only the case directory is normalized to CASE; metadata roles
and hook arguments/environment remain distinct.

## Validation and remaining work

verify.py checks complete matrix, recomputed comparisons, actual phase/refusal
controls, ref publication/preservation, OID widths, contents, common vs linked
metadata, bare index absence and 287 source SHA-256 hashes. Hashes cover 279 product
Python files, original SEC-11 race and host-exec test, four SEC-13 Python artifacts,
and runner/verifier, checked at runner completion and verification. Repeated
verification is byte-identical; syntax and staged whitespace checks pass.

An initial run deliberately failed its source-integrity assertion because the
verifier was edited while that run was active. An intermediate verifier also
rejected the observed post-index-change preceding pre-commit. The final saved run
uses stable runner/verifier bytes and checks the observed phase order explicitly.

No product, existing security assertions, policy, runtime routing or approval
mechanism changed. No daemon/service-journal, full core/UI, coverage, performance,
CI/install or independent host security acceptance was run. Evidence-integrity
PASS is not compatibility or security PASS. Stage A still needs sequencer recovery
message/index edits and RPC/service-journal confirmation of consequential earlier
differences. This repository-origin matrix does not extend every previous operation
to all origins/formats. Stage B remains an isolated immutable installed-runtime
prototype with unchanged SEC-11/13 probes. Live-worktree publication remains
unresolved; no shell/interpreter exception for the ordinary worker is authorized.
