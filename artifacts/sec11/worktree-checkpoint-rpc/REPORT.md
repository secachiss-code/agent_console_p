# SEC-26: worktree and checkpoint restore through daemon RPC

Base: `1fcc8961dc98cf1623ace5d3259577b81039af2d` (SEC-25).
SEC-11/13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and scope

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/worktree-checkpoint-rpc/run.py > artifacts/sec11/worktree-checkpoint-rpc/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/worktree-checkpoint-rpc/verify.py > artifacts/sec11/worktree-checkpoint-rpc/validation.json
```

48 fresh disposable cases: SHA1/SHA256 × ordinary worktree creation, detached
isolated executor creation, checkpoint restore × observe, selected reference
prepared refusal, selected reference committed failure, post-checkout failure
× native/daemon. Git/Python versions and the base revision are recorded in JSON.

The assembled Daemon.serve uses a real Unix socket, authenticated UI RPC,
HostTabs/Worktrees/GitCheckpoints and SQLite. Existing test fixtures provide a
fake Podman executable, without launching an agent or terminal. tab/open uses
start_now=False. An isolated executor first opens a primary main-checkout tab,
then opens a second tab in that work session with isolated=True. It therefore
uses the real session worker start ref, unlike SEC-19's low-level executor ref.
No setup script or .worktreeinclude is present. All paths belong to temporary
fixtures. The user's daemon and repositories are not contacted.

For restore, a primary tab is opened first. The initial legacy checkpoint is
seeded explicitly through the daemon's CheckpointService on its tab worker,
then discovered by checkpoint/list. checkpoint/restorePreview supplies the real
confirmation token for checkpoint/restore. There is no claimed capture/delete
RPC: those endpoints do not exist. The measured restore action includes a new
safety capture and journal write before materialization. This is a compound
service action, unlike SEC-19's materialize-only case. It does not exercise
prompt ingestion, retention deletion or checkpoint pruning.

Fresh native controls reuse SEC-19's fixture and corresponding Git plumbing.
Native restore captures a second checkpoint before restoring the first, so it
also tests the safety publication refusal. Its refs are fixed fixture refs;
RPC refs contain generated tab/session IDs. The hook selector matches explicit
application ref prefixes, including SEC-19's executor prefix; recorded stdin
and selected rows are independently checked by the verifier. Equal all-zero
rows are retained. This fixture selector is not an authorization policy.
Native has no application journal, ownership record or session creation step.

HOME, XDG, global/system Git configuration and inherited Git settings are
isolated before daemon imports. Identities/dates are pinned by the fixtures and
environment. Snapshots disable hooks and capture HEAD, source index entries and
bytes, refs, checkpoint trees and tree OIDs, files, registration, locks and
ownership metadata. SQLite tabs/workspaces/work_sessions/checkpoints/git_operations
are read before/after the measured RPC and again after graceful daemon stop.
Private case roots are normalized; hook private index names are labeled
PRIVATE_INDEX. Cross-repository index byte equality is not claimed.

The final run executes outside the command sandbox inside the agent container,
with explicit approval. It does not run on the host or install a runtime. The
sandboxed attempt stalled after migrations and was interrupted; its log is
retained as sandbox-attempt.log, without a matrix acceptance claim or a diagnosis
of the stall. A separate 24-case native preflight passed. The first full run
exposed a verifier mistake comparing SQLite tree_sha to a commit OID; the runner
now also records tree OIDs and a fresh full run uses that corrected comparison.

## Measured behavior

All 48 cases complete. Outcomes agree in 12/24 pairs, and phase/argument
sequences agree in 0/24. Native hooks run in every case; daemon actions emit no
instrumented hooks. Each operation has a real prepared refusal and post-checkout
failure positive control in each object format. Committed failures retain native
success. Daemon actions succeed in every case, consistently with the current
hook-disabled GitRunner contract; this is not a daemon security bypass finding.

Native prepared refusal prevents branch creation, detached safety-ref publication,
or restore's safety-checkpoint publication. The original refs/files remain.
Daemon creation and restore succeed under the same hook instrumentation.
Native post-checkout failure happens after both worktree creation paths have
created a registered worktree. Native restore changes tracked.txt, then errors
before restoring untracked.txt or removing extra.txt. Daemon restore completes
both restores and removes extra.txt. Thus failure does not mean no mutation.

Source HEAD, symbolic HEAD, index entries and exact index bytes remain unchanged
within every case, as do ignored content and the source runtime lock. Successful
restore returns the captured worktree contents while preserving the staged
index. Checkpoint trees exclude ignored.txt and .aconsole/lock. Daemon worktrees
have the expected tracked content, lock, registration and ownership records.
Native/plain Git intentionally lacks the AConsole ownership artifacts.

Every daemon restore has one initial checkpoint row and two final checkpoint
rows, including the safety checkpoint returned by RPC. Both row tree OIDs match
recorded Git tree OIDs. checkpoint/list returns both records. Daemon creation
persists one tab, or two for the isolated case, with the destination registered
in the workspace row. Recorded tables are identical after graceful daemon stop.
No git_operations row is created by these paths: their durability evidence is
in workspace/session/checkpoint tables, not the sequencer journal. There is no
restart/new-client or crash/in-flight recovery claim.

## Verification and remaining work

verify.py recomputes complete coverage, comparisons, current source hashes,
refusal selection, native positive controls, source/index/ignored preservation,
partial native restore, daemon content/ownership and SQLite/refs consistency.
validation.json's evidence_integrity PASS means these assertions hold, not hook
compatibility or security acceptance. All 293 source hashes match. Repeated verification is byte-identical; Python
syntax and staged whitespace checks pass. Product sources, policy, routing,
existing tests and original SEC-11/13 probes are unchanged.

This matrix covers ordinary populated source checkouts. It does not cover
worktree removal/pruning, dirty close, linked/bare/unborn origins, real Podman,
installed runtime, live UI, approval interaction, malicious hooks, containment,
crash recovery or deletion-boundary attacks. Native restore uses flat ordinary
fixture files. No full core/UI, coverage, CI/install, performance or independent
security gate ran.

Required-helper findings from SEC-20 and repository-origin findings from SEC-21
still need daemon evidence. Capture/delete-only API findings from SEC-19 are
not promoted to RPC acceptance here. Stage B still needs the isolated immutable
installed runtime and unchanged SEC-11/13 probes, with no shell/interpreter
exceptions. Publication into a live worktree remains unresolved. SEC-26 does not
close SEC-11/13 or stage A.
