# SEC-27: required filters and custom merge helpers through daemon RPC

Base: `85fa46c0293cae0ef580595ea0ace487434ef205` (SEC-26).
SEC-11/13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction and scope

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/helper-rpc/run.py > artifacts/sec11/helper-rpc/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/helper-rpc/verify.py > artifacts/sec11/helper-rpc/validation.json
```

32 fresh disposable cases: SHA1/SHA256 × stage, restore, merge, merge preview ×
helper success/refusal × native/daemon. Native controls reuse SEC-20's fixture
and commands, executing fresh repositories rather than replaying saved results.
Git/Python versions and base revision are recorded in results.json.

The assembled Daemon.serve runs with authenticated UI requests over a real Unix
socket and a real SQLite database. Existing test fixtures provide fake Podman;
no agent, terminal or user's daemon is launched. stage uses git/state freshness
values and git/stage. Restore uses git/restoreFilePreview's confirmation token
and git/restoreFile, including its real safety-ref capture. Merge cases use
git/mergePreview; the merge scenario also submits git/mergeWorktree with the
returned token to measure the service's refusal. Preview-only cases do not
submit a mutation. No low-level product call substitutes for the daemon path.

HOME/XDG/global/system Git configuration and inherited Git settings are isolated
before importing daemon fixtures. Identities and dates are pinned. Configuration
enables required=true with both clean and smudge commands, or a custom merge
driver, after committing the initial canonical fixture. Helpers record actual
inputs and refusal decisions. Successful clean changes WORK: to CLEAN:, smudge
does the inverse, and the merge helper resolves divergent base/ours/theirs into
RESOLVED:ours+theirs. Refusal exits before writing output. Merge input temporary
names are normalized by the native fixture, with original semantic inputs saved.

The sandboxed attempt stalled after migrations and ended with timeout exit 124;
its retained sandbox-attempt.log has no matrix acceptance claim. The completed
run was explicitly approved outside the command sandbox inside the agent
container. It does not run on the host or install a runtime. Saved console logs
have trailing padding removed; the JSON evidence is unchanged.

## Measured behavior

| Operation | Native helper success | Daemon, either helper mode |
| --- | --- | --- |
| stage | index contains CLEAN:updated | success, index contains WORK:updated |
| restore | worktree contains WORK:base | success, worktree contains CLEAN:base |
| merge preview | clean candidate with resolved content | conflict, no available merge modes |
| merge | commit contains resolved content | MERGE_CONFLICT before a journal operation is created |

Outcomes agree in 8/16 pairs; final data.txt contents agree in 10/16. Equality
of a boolean outcome does not establish equivalent contents or operation phases.
Each of the 16 native cases invokes exactly one helper with the expected input;
all 16 daemon cases emit no helper trace, including daemon startup, discovery,
preview and safety capture. Both object formats have the same semantic results.

Required clean refusal makes native stage fail and retain its original index
and file. Daemon stage succeeds with untransformed bytes even when the configured
helper would refuse. Required smudge refusal makes native restore fail after
removing data.txt; daemon restore succeeds with canonical stored bytes. These
confirm SEC-20's compatibility findings through the public service layer.
They do not demonstrate escape from the Git execution boundary.

Daemon restore preserves index entries and exact index bytes and adds one
safety ref whose data.txt contains WORK:updated. That safety content preserves
the pre-restore worktree bytes, also without clean transformation. The original
HEAD/tree and existing refs remain unchanged. Ignored and untracked sentinels
survive every case. Safety capture is a compound part of this service operation;
there is no claim that native restore creates an equivalent recovery ref.

Native successful merge/preview really executes the custom merge helper and
produces the resolved content. Native refusal yields a conflict. For merge,
the saved native intermediate state has MERGE_HEAD and unresolved index;
the runner then explicitly performs reset --merge, matching SEC-20's cleanup.
This is not an automatic rollback claim. Daemon preview reports conflict even
when native can resolve it; mergeWorktree returns MERGE_CONFLICT and preserves
HEAD, refs, index entries/bytes and files. This matrix cannot claim execution
of the daemon merge writer after a successful helper-aware preview, since the
current preview blocks the operation.

All git_operations queries before, after and after graceful daemon stop return
zero rows: stage/restore do not use that journal and merge is refused before
creating a record. Snapshots after stop equal those after RPC; startup snapshots
also equal those before daemon startup. This is persisted-state observation,
not restart/crash recovery acceptance. Read commands and merge preview may write
objects; no unreachable-object or object-database rollback census is claimed.

## Validation and remaining work

verify.py checks complete coverage, source hashes, recomputed comparisons,
native positive controls, contents, OID widths, safety refs, index preservation,
merge refusal and SQLite evidence. Evidence integrity PASS covers those
assertions; it does not mean helper compatibility or security acceptance.
All 295 recorded source hashes match, including product Python, reused fixtures,
original SEC-11 probes, SEC-13 artifacts, runner and verifier. Repeated verifier
output is byte-identical; Python syntax and staged whitespace checks pass.

No product sources, policy, existing tests, routing or security assertions change.
This matrix excludes linked/bare/unborn origins, filter-process protocol, partial
helper writes, recursive merges, external configuration/attribute races, real
Podman, installed runtime, live UI, approval interaction, malicious-helper
containment, full core/UI, coverage, CI/install, performance and independent
host-boundary security acceptance. Repository origins from SEC-21 still need
daemon evidence. Capture/delete-only paths from SEC-19 remain limited acceptance.

Required-helper compatibility still needs an explicit decision: the existing
neutralization silently stages unfiltered content. Stage B still requires an
isolated immutable installed runtime with unchanged SEC-11/13 probes and no
shell/interpreter exceptions. Live-worktree publication remains unresolved.
SEC-27 closes its evidence task only, not SEC-11/13 or stage A.
