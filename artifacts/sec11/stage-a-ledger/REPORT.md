# SEC-29: stage A acceptance ledger

Base: `2609cbe09935112a23ba540f0a913b53c1b9f48e` (SEC-28).

Stage A remains **INCOMPLETE**. SEC-11 and SEC-13 remain failed. This ledger
reconciles existing evidence, and does not implement or authorize stage B.
No product source, existing probe, approval, host policy or runtime routing changes.

## Result and provenance

The machine-readable [ledger](ledger.json) maps ten criteria to SEC-15..28 and
GIT-6, with remaining work and six explicitly classified compatibility decisions.
[evidence.json](evidence.json) pins 90 artifact files and all 279 product Python
files. Artifact paths are repository-relative. Each catalog entry retains its
original base SHA, case count, observed hook/helper phases and source-hash drift.
The base SHA identifies code inspected now; an earlier experiment is not relabeled
as a fresh run on that SHA. Git 2.47.3/Python versions remain in the original
result files; this task does not execute new native/product scenarios.

One historical snapshot differs: SEC-15's `git/operations.py` predates GIT-6.
Its successful ref-only squash with a silently dropped staged hook file is
historical evidence, superseded for current behavior by GIT-6's RPC refusal.
All other SEC-16..28 and SEC-17 product/source snapshots match the recorded bytes.
GIT-6's five focused RPC cases and 132 related tests are saved historical results,
not newly executed tests. Hash agreement preserves relevance, not freshness of
environment or independent acceptance authority.

## Criteria reconciliation

### A-command: Machine-readable runner callers/commands and direct _run_hook

State: `bounded_inventory`. Evidence: [SEC-17](../command-inventory/REPORT.md).

Eight runner constructions/import sites and manually reviewed wrappers/dynamic argv plus direct hook subprocess are indexed. Product bytes still match.

Remaining: Scanner is syntactic, not an executable allowlist. Stage B needs typed command requests and explicit supported-operation scope; submodule/signing/filter-process helper requirements have no positive census.

### A-commit: Differential commit including root/amend

State: `measured_with_limits`. Evidence: [SEC-15](../hook-census/REPORT.md), [SEC-16](../hook-census-extended/REPORT.md), [SEC-28](../repository-origins-rpc/REPORT.md).

Ordinary/root/amend hook traces exist; linked/unborn stage+commit and refusal use real daemon RPC on SHA1/SHA256.

Remaining: SEC15 predates GIT6. Amend is a GitRunner probe, not a public operation. No full RPC matrix for ordinary commit message/index edits; linked/unborn hook-edit cases absent.

### A-merge: Differential merge, squash, fast-forward and no-worktree target

State: `compatibility_difference`. Evidence: [SEC-15](../hook-census/REPORT.md), [GIT-6](../../git6/REPORT.md), [SEC-16](../hook-census-extended/REPORT.md), [SEC-24](../merge-refusal-rpc/REPORT.md).

Ordinary operations traced. Direct no-checkout merge/squash omit prepare/reference/post phases; installed fallback cannot find git-sh-setup. GIT6 now refuses changed candidate index in ref-only squash.

Remaining: Choose and specify supported no-checkout phases, fallback runtime/dependencies and changed-candidate review. Ordinary merge/squash/FF evidence is low-level; no fresh full RPC edit matrix. Do not equate a bare project with a target ref lacking a checkout.

### A-refusal: Existing hook refusals and post-success failures accounted for

State: `compatibility_difference`. Evidence: [SEC-16](../hook-census-extended/REPORT.md), [SEC-18](../sequencer-census/REPORT.md), [SEC-19](../worktree-checkpoint-census/REPORT.md), [SEC-24](../merge-refusal-rpc/REPORT.md), [SEC-25](../sequencer-refusal-rpc/REPORT.md), [SEC-26](../worktree-checkpoint-rpc/REPORT.md), [SEC-28](../repository-origins-rpc/REPORT.md).

Native prepared refusal and late-failure controls recorded; target-specific RPC selection removes ORIG_HEAD masking. Daemon can succeed where native refuses because phases are absent.

Remaining: Specify refusal semantics per supported operation. Retain native positive controls, partial mutation on error and completed-publication status; broad refusal or absent phase is not enforcement acceptance.

### A-message: Message editing accounted for

State: `compatibility_difference`. Evidence: [SEC-15](../hook-census/REPORT.md), [GIT-6](../../git6/REPORT.md), [SEC-22](../sequencer-edits/REPORT.md), [SEC-23](../sequencer-rpc/REPORT.md).

Commit-msg edits propagate in original commit/merge paths and GIT6 RPC control. Native sequencer prepare/commit-msg edits differ from daemon because daemon emits no hooks.

Remaining: Specify phase/message handling for sequencer and no-checkout paths. Current evidence is not authenticated immutable-bundle approval acceptance.

### A-index: Hook index mutations accounted for

State: `compatibility_difference`. Evidence: [SEC-15](../hook-census/REPORT.md), [GIT-6](../../git6/REPORT.md), [SEC-22](../sequencer-edits/REPORT.md), [SEC-23](../sequencer-rpc/REPORT.md).

Original ref-squash loss is historical; GIT6 refuses candidate tree change before publication. Native sequencer staged edits differ from daemon missing phases.

Remaining: Retain GIT6 refusal and file preservation. Define candidate re-review and artifact retention before broker publication. Native merge fixture also omits newly staged pre-merge file; do not impose squash behavior on merge.

### A-recovery: Recovery and restart accounted for

State: `measured_with_limits`. Evidence: [GIT-6](../../git6/REPORT.md), [SEC-23](../sequencer-rpc/REPORT.md), [SEC-24](../merge-refusal-rpc/REPORT.md), [SEC-25](../sequencer-refusal-rpc/REPORT.md), [SEC-26](../worktree-checkpoint-rpc/REPORT.md).

SQLite/safety-ref transitions recorded; SEC25 has 120 graceful restarts with fresh daemon/client, including 48 conflicts. GIT6 retries after explicit refusal preserve failed row.

Remaining: No in-flight crash, power loss, worker cancellation, exactly-once grants or live-worktree publication acceptance. These are stage C/D gates; graceful stop is not crash recovery.

### A-phases: All actually invoked hook phases accounted for

State: `partial`. Evidence: [SEC-15](../hook-census/REPORT.md), [SEC-16](../hook-census-extended/REPORT.md), [SEC-18](../sequencer-census/REPORT.md), [SEC-19](../worktree-checkpoint-census/REPORT.md), [SEC-21](../repository-origins/REPORT.md), [SEC-22](../sequencer-edits/REPORT.md), [SEC-23](../sequencer-rpc/REPORT.md), [SEC-24](../merge-refusal-rpc/REPORT.md), [SEC-25](../sequencer-refusal-rpc/REPORT.md), [SEC-26](../worktree-checkpoint-rpc/REPORT.md), [SEC-28](../repository-origins-rpc/REPORT.md).

Phase union is indexed from raw traces below. Reference transactions include prepared/committed/aborted and AUTO_MERGE zero-OID cleanup; index and per-file checkout events retained.

Remaining: Trace union is not proof of completeness across all installed hooks/command variants. Fallback sample entry is uninstrumented. Worktree removal/prune, prompt capture/retention-delete, signing and submodule configurations remain unclassified for a supported command contract.

### A-helper: Required helper compatibility and native positive controls

State: `compatibility_difference`. Evidence: [SEC-20](../helper-census/REPORT.md), [SEC-27](../helper-rpc/REPORT.md).

Daemon stage succeeds with WORK bytes instead of CLEAN, restore yields canonical bytes, custom merge preview conflicts; no daemon helper trace.

Remaining: Choose explicit fail-closed unsupported-helper diagnostic or isolated helper implementation before routing. Silent required-filter neutralization is unacceptable under DESIGN; do not add shell/interpreter exceptions.

### A-origins: Unusual repository scope and service reachability

State: `measured_with_limits`. Evidence: [SEC-19](../worktree-checkpoint-census/REPORT.md), [SEC-21](../repository-origins/REPORT.md), [SEC-26](../worktree-checkpoint-rpc/REPORT.md), [SEC-28](../repository-origins-rpc/REPORT.md).

Linked/unborn RPC covered for stage+commit on both formats. Bare project/add rejected in all six cases; bare plumbing evidence is low-level only. Checkpoint restore includes safety capture via service.

Remaining: No standalone checkpoint capture/delete RPC exists; measure actual prompt/retention entrypoints or explicitly exclude them. Bare low-level move_ref is not reproduced through project/add. No universal no-checkout service coverage claim; hostile gitdirs/alternates/config races remain stage B attacks.

## Phase accounting

These are the eleven instrumented hook phases actually found in saved traces,
not a claim to enumerate every Git hook or every variant of a command:

| Hook phase | Evidence tasks with recorded events |
| --- | --- |
| commit-msg | SEC-15, SEC-16, SEC-18, SEC-21, SEC-22, SEC-23, SEC-24, SEC-25, SEC-28 |
| post-commit | SEC-15, SEC-16, SEC-18, SEC-21, SEC-22, SEC-23, SEC-24, SEC-25, SEC-28 |
| post-index-change | SEC-15, SEC-16, SEC-18, SEC-19, SEC-21, SEC-22, SEC-23, SEC-24, SEC-25, SEC-26, SEC-28 |
| post-merge | SEC-15, SEC-16, SEC-24 |
| pre-commit | SEC-15, SEC-16, SEC-18, SEC-21, SEC-22, SEC-23, SEC-24, SEC-25, SEC-28 |
| pre-merge-commit | SEC-15, SEC-24 |
| prepare-commit-msg | SEC-15, SEC-16, SEC-18, SEC-21, SEC-22, SEC-23, SEC-24, SEC-25, SEC-28 |
| reference-transaction | SEC-15, SEC-16, SEC-18, SEC-19, SEC-21, SEC-22, SEC-23, SEC-24, SEC-25, SEC-26, SEC-28 |
| post-rewrite | SEC-16, SEC-18, SEC-22, SEC-23, SEC-25 |
| post-checkout | SEC-18, SEC-19, SEC-22, SEC-23, SEC-25, SEC-26 |
| pre-rebase | SEC-18, SEC-22, SEC-23, SEC-25 |

Clean, smudge and merge helper events are indexed separately in `helper_evidence`;
they are not hook phases. The installed pre-merge fallback sample is not itself
instrumented: downstream pre-commit or a shell error is the observed control.
Reference-transaction phase arguments and stdin remain in raw results, including
prepared/committed/aborted, target-specific refusals and zero-OID AUTO_MERGE
cleanup. `post-index-change` and per-file `post-checkout` cannot be replaced by
one ordinary commit hook pair. No observed event proves phase completeness in
uninstrumented paths or dependency capture for sourced/helper code.

## Decisions before routing

The proposals below are reviewable follow-up work, not recorded user approval.
The only implemented policy here is the existing GIT-6 changed-candidate refusal.

### D-helper (`unresolved`)

First isolated worker rejects operations requiring external filters/drivers with a specific unsupported-helper diagnostic; no unfiltered success. Decide supported helpers separately.

Acceptance: Required clean/smudge success/refusal controls; contents and partial-write state asserted through actual service; no host fallback.

### D-phases (`unresolved`)

Publish a finite operation/phase contract for the initial isolated broker. Installed hooks outside supported phases block with a diagnostic; no silent skipping.

Acceptance: Resolve no-checkout prepare/reference/post semantics and fallback dependencies; compare phases/argv/env/stdin and refusal outcomes with native controls.

### D-candidate (`existing_GIT6_policy_only`)

Keep ref-only squash refusal when hooks change the preview candidate; future broker requires review of changed candidate before publication.

Acceptance: GIT6 assertions unchanged; staged/unstaged/untracked/ignored data survives; no claim that temporary index is retained or arbitrary hook side effects rolled back.

### D-post (`design_requirement_unimplemented`)

Record successful publication and post-hook failure separately; never retry mutation automatically on late failure.

Acceptance: Stage C/D journal crash/cancel tests and one-shot persisted grants; existing graceful restarts are insufficient.

### D-scope (`unresolved`)

Bound the first isolated prototype to explicitly enumerated commands in detached candidates. Exclude unsupported command/helper/origin variants explicitly instead of extending census without a gate.

Acceptance: Typed adapter and deny cases for excluded operations; classify prompt capture/retention-delete and removal/prune entrypoints; no production routing.

### D-publication (`unresolved`)

No live-worktree publication from first isolated prototype; retain SEC14 reviewed publication/recovery gate.

Acceptance: Stage D crash injection, concurrent external writes, index/files/ref reconciliation and exact grant consumption.

## Bounded next step and stage boundaries

Create a separate stage-A scope/compatibility contract for D-helper, D-phases
and D-scope: enumerate the first supported commands and their phases, explicitly
exclude unmeasured variants, and specify unsupported-hook/helper diagnostics.
Resolve actual prompt-capture/retention-delete and worktree removal/prune caller
reachability from source; do not invent capture/delete RPCs or turn bare project
rejection into universal no-checkout acceptance. Any further experiment must close
a named contract criterion; an ever-growing census alone cannot establish the gate.
These decisions and the unresolved phase scope prevent a stage-A completion claim.

Stage B separately requires an installed immutable isolated worker and unchanged
SEC-11 race/SEC-13 post-ready replace/include/worktree/inplace probes, executable/
FD/ABI/descendant attacks and external canaries. Do not grant shell/interpreter
exceptions. Stage C needs immutable bundles, authenticated one-shot grants and
sandboxed hooks. Stage D needs reviewed publication and crash/cancellation recovery;
graceful restarts do not satisfy that gate. Stage E needs coordinated runner/direct
hook routing plus core/UI/contracts and independent installed host acceptance.
No filesystem/process/network containment follows from this ledger or its verifier.

## Validation and limits

`python3 artifacts/sec11/stage-a-ledger/collect.py` emits a deterministic snapshot
without product imports or mutations. Compare with evidence.json before accepting
any future source drift. `python3 artifacts/sec11/stage-a-ledger/verify.py` checks
snapshot equality, base-commit product blobs, all evidence/decision/criterion
references, exact observed phase union, historical drift and GIT-6 saved JUnit
counts. `validation.json` is its output; evidence-integrity PASS is local integrity
only, not stage acceptance. The verifier is run twice for byte-identical output.

`reverification.json` records fresh, read-only execution of the thirteen existing
SEC-16..28 evidence verifiers, including the SEC-17 static inventory recomputation.
Reproduce with the existing locked environment:
`/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/stage-a-ledger/reverify.py`.
All thirteen exit zero with evidence-integrity PASS. The initial system-Python
attempt lacked msgspec for seven verifiers; it is retained in
`system-python-attempt.json`. The complete rerun uses the existing core environment,
with no dependency installation. Some old verifiers import fixture/product
modules to recompute comparisons; no new operation matrix is executed.
The original SEC-15 verifier is not rerun against changed product bytes. GIT-6
product tests and original SEC-11/13 attack probes are not rerun. Python syntax
and staged whitespace are checked. No new full core/UI, coverage, CI, install,
performance, real Podman, live UI, independent review or host security PASS is
claimed. The inherited command-sandbox daemon stall remains undiagnosed; this
read-only reconciliation neither reproduces nor diagnoses it.
