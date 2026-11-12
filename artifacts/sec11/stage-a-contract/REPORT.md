# SEC-30: bounded first Git worker contract

Base: `f2a964334ea4b16fce3729fe2b1ddf5ee87e5077` (SEC-29).
Status: **specified, not implemented**. Stage A remains **INCOMPLETE**;
SEC-11 and SEC-13 remain failed. No runtime, product source, routing, host
permission, existing probe or previous evidence has changed.

[contract.json](contract.json) specifies the first worker scope and concrete
acceptance cases. [source-index.json](source-index.json) pins all 279 product
Python files, 56 function anchors and upstream ledger/design/inventory evidence.
This is a local source review and compatibility decision for a future prototype,
not user approval, installed security acceptance or an executable allowlist.

## Initial scope and compatibility decisions

Only two requests are planned, on a **fresh standalone disposable snapshot**:

| Request | Exact command shapes | Result |
| --- | --- | --- |
| InspectCandidate | `status --porcelain=v2 -z --branch --untracked-files=all --ignore-submodules=none` | Bounded status records, read-only candidate access |
| StageCandidatePaths | `--literal-pathspecs add -A -- <paths...>` then `write-tree` | Candidate index/tree digest; no commit/ref mutation |

The first request derives from GitHistory.status; the second derives from
GitOperations.stage / GitMutations.stage and the staging portion of
GitCheckpoints.capture. Those callers are **not routed**. Neither request is a
replacement for checkpoint capture or a general GitRunner.read/write method.
The command templates are future adapter inputs, not shell commands for callers.
Inputs contain only a registered candidate ID, expected immutable snapshot digest
and validated literal paths for staging. Unknown requests/options/fields, raw argv,
caller environment and executable paths reject. Proposed limits are explicit in
the contract and remain untested; missing enforcement rejects the runtime.

SHA1/SHA256 detached and unborn standalone candidates are in the planned scope.
Original linked/bare repositories, external gitdirs/alternates, submodules,
sparse/split indexes, and target-ref publication without a checkout are excluded.
An isolated candidate is not `git worktree add --detach` in a shared live object
store. A trusted, consistent snapshot importer is still required. Snapshot input
config/includes/attributes/ignore rules and files must be pinned together; changes
or ambiguous dependency capture reject before execution. No live-project mount,
copy-back, branch update or new host access follows from this contract.

**D-helper:** reject external filter/driver requirements with
`UNSUPPORTED_HELPER`, including optional/process filters and custom merge,
signing, fsmonitor, external diff/textconv and config-selected commands. This
conservative policy also rejects unused configured helpers. In particular, do not
return staging success with raw WORK bytes instead of required CLEAN bytes as
observed in SEC-20/27. Do not strip helper semantics and relabel it compatibility.
Config discovery is not confinement: late injection still requires the immutable
runtime and unchanged attack probes in stage B. No shell/interpreter exceptions.

**D-phases:** the worker executes **zero hook phases**. Any installed executable
hook, including an unknown filename or executable sample, blocks both requests
with `UNSUPPORTED_HOOK`; non-executable files are inert data. Non-default
core.hooksPath and uncertain dependency capture reject. All eleven observed
phases from SEC-29 remain enumerated, including post-index-change, checkout,
rewrite and reference transactions. Their absence is an explicit unsupported
case, not claimed parity. A captured hook inventory must remain valid during use;
checking mutable paths once is insufficient.

Commit/root/amend, merge/squash/FF, no-checkout merge, rebase/cherry-pick and
continue/abort are excluded entirely. Thus omitted prepare-commit-msg,
reference-transaction and post phases cannot silently produce success. The
installed pre-merge fallback sample's git-sh-setup dependency is unsupported;
no helper or shell exception is added to make it execute. Future hook/broker
support needs its own expanded phase/argv/env/stdin and dependency contract.

**D-scope:** eleven excluded families link to current callers and SEC-29 evidence.
Unstage/restore, snapshots and retention, worktree lifecycle, history/diff/merge
preview, MCP/clipboard/discovery/trailers and generic runner interfaces do not
enter this prototype. InspectCandidate does not authorize arbitrary nominally
read operations: merge-tree can write objects and reach merge helpers.

**D-candidate:** existing GIT-6 ref-only squash changed-index refusal remains
untouched. It does not establish retained temporary indexes or rollback of
arbitrary hook side effects. A future broker must retain a changed tree/message,
obtain new review and a new execution grant. **D-post/D-publication:** no
publication or post hook executes here; later C/D gates still need durable
one-shot grants, completed-publication versus late-failure state, stale-OID CAS,
crash/cancel recovery and protection of concurrent live files. No automatic retry
of a consumed hook. Excluding these operations does not close the broader stage A.

## Source reachability, not invented RPCs

Every function below is bound to a file, line span, digest and checked source
fragments in the index. These are manually reviewed call paths, not a complete
static call graph or new native/daemon measurements.

| Lifecycle | Actual source path | Disposition |
| --- | --- | --- |
| Prompt legacy snapshot | bootstrap._open_ingest wires IngestHandlers.on_prompt; consumer.submitted_prompt recognizes UserPromptSubmit; consume invokes bootstrap._prompt_sent -> ReviewService.on_prompt -> CheckpointService.on_user_prompt -> GitCheckpoints.capture | Excluded; enabled and existing-tab guards apply |
| Prompt session snapshot | ReviewService.on_prompt -> TabService.session_checkpoint -> WorkSessionHistory.capture/prepare/complete -> GitCheckpoints.capture | Excluded; active work-session/history branch; separate from legacy snapshot |
| Keep-count removal | on_user_prompt -> CheckpointService._drop -> GitCheckpoints.delete, then journal deletion | Excluded; protected refs are skipped |
| Retention outbox | _open_database starts _sweep_loop -> _sweep_once -> sweep/_queue_ref -> durable GitRefDeletion; _drop_swept_refs -> drain_ref_deletions -> GitCheckpoints.delete; ReviewService.forget_refs -> CheckpointService.forget | Excluded; failed jobs stay queued, protected/session refs guarded; known-tab forget may repeat deletion |
| Legacy tab close | close_tab -> _finish_tree -> HostTabs.remove_worktree -> Worktrees.close/_remove | Excluded; stop/decision/created_here/dirty/ahead/ignored guards; session close retains workspace |
| Explicit Git cleanup | GitCleanup.cleanup -> GitMutations.remove_worktree, then optional owned branch deletion | Excluded; distinct from Worktrees.close |
| Create rollback | Worktrees.create/_roll_back or create_detached cleanup -> force removal | Excluded; separate failure path, not normal close policy |
| Metadata prune | Worktrees.prune -> worktree prune and stale metadata unlink | Excluded; no production attribute caller found in current src scan |

Capture uses private index read-tree/add/update-index/write-tree, then
commit-tree/update-ref; delete uses update-ref -d. Materialize also deletes
ordinary filesystem paths. StageCandidatePaths provides none of the complete
capture/restore/delete transaction. ReviewService.dispatch exposes only
checkpoint/list, checkpoint/restorePreview and checkpoint/restore for checkpoints;
there is no standalone capture/delete RPC. The prompt and retention paths must
be tested via their real callbacks/sweeper before a future scope expansion.
Session refs do not enter automatic legacy retention. No exactly-once ref-delete
claim follows from the durable queue or its repeated deletion path.

The saved AST scan records every `.prune(...)` call in src and tests/core.
The two src calls are ApprovalBook.self.prune; four worktree calls are in
tests/core/test_worktrees.py. This bounded name-based scan does not establish
absence of reflection, plugins, injected receivers or installed-code differences.
Worktree removal/prune hook behavior remains unmeasured and explicitly excluded;
SEC-26 create/restore evidence is not relabeled as removal/prune acceptance.
Bare project/add rejection likewise does not prove all no-checkout services safe.

## Finite acceptance and next action

Five named, still-unrun compatibility criteria precede any runtime promotion:

- T-positive: native/worker candidate status and stage parity on both formats,
  detached/unborn, add/modify/delete and literal unusual paths; tree/index/file
  assertions and preservation of ignored/untracked files.
- T-helper: required filter/custom merge native positive and refusal controls;
  candidate worker diagnostic and unchanged preflight state, never raw-byte success.
- T-hooks: every observed phase plus unknown/fallback/mode/path cases; no worker
  hook marker; executable samples reject and non-executable controls remain usable.
- T-input: malformed paths, stale snapshots, unknown fields/env, unsupported
  origins and dependency capture; no writes to original files/metadata.
- T-deny: each excluded operation/lifecycle, with no current-runner fallback.

Only a separate bounded fixture node is the next step. No additional open-ended
census is requested. Later gates T-isolation (B), T-candidate (C/D) and
T-publication (C/D/E) remain separate: installed immutable enforcement, unchanged
SEC-11/13 probes and positive controls, executable/FD/ABI/descendant attacks,
canaries/host sockets, resource bounds, approved immutable bundles, durable grant
consumption, crash/cancellation/publication recovery, coordinated routing and
independent installed acceptance. Specifying a smaller subset does not waive
those gates or promote the original full compatibility matrix to PASS.

## Reproduction and limits

`python3 artifacts/sec11/stage-a-contract/collect.py` prints the deterministic
source/evidence index without importing product code. Redirect to a temporary
file and compare before replacing the saved snapshot on any future source drift.
`python3 artifacts/sec11/stage-a-contract/verify.py` verifies exact snapshot
recomputation, current product against SEC-29 hashes and actual base git blobs,
source/evidence links, phase accounting, explicit scope and lifecycle dispositions.
Seven negative controls must reject stage/routing promotion, lost phase/caller,
raw argv, helper success and expanded command templates. `validation.json` retains
the result and hashes of these artifacts. Repeated output must be byte-identical.

Local integrity PASS means artifact consistency only. The verifier does not
execute a request policy, Git mutation, attack, native control or daemon scenario.
The machine contract is agent-editable documentation, not trusted runtime policy.
No full product suite, UI, coverage, CI/install, performance, new security probe
or independent review ran. Historical source hashes preserve provenance, not
freshness of the old environments. The inherited sandbox daemon stall is still
undiagnosed. Original probes and all existing product bytes remain unchanged.
