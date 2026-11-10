# SEC-20: required filters and custom merge helpers

Base: `338606da993e7f866772767afc5591a8b4f38ddc` (after SEC-19).
Container experiment with Git 2.47.3; exact Python version in results.json.
SEC-11 and SEC-13 remain failed; SEC-14 stage A remains incomplete.

## Reproduction

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/helper-census/run.py > artifacts/sec11/helper-census/run.log 2>&1
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/helper-census/verify.py > artifacts/sec11/helper-census/validation.json
```

32 disposable repositories: SHA-1/SHA-256 × stage/restore/merge/merge preview ×
helper success/refusal × native/product. Product uses GitMutations.stage,
restore_file, merge and GitHistory.merge_probe. Native uses matching Git commands.
Both use the same committed attributes, repository config, pinned identities and
dates, isolated HOME/XDG/global/system config and cleared inherited GIT_* variables.
Helpers are local Python programs whose argv, input bytes (text-only fixtures),
merge input file contents and refusal decisions are saved. They run only inside
the disposable container experiment; no host helper permission is introduced.

Fixture initialization commits canonical contents before configuring helpers,
so setup cannot satisfy the positive control accidentally. Clean transforms
WORK: to CLEAN:, smudge performs the inverse. Both filter commands are present
and filter.census.required=true. The merge fixture has genuinely divergent
single-line contents base/ours/theirs and merge=census in committed attributes.
Its helper reads all three inputs and writes RESOLVED:ours+theirs into %A.
Failure exits before producing output or modifying %A. Long-running filter
process protocol, helper partial-write failures and recursive merges are outside
this matrix.

## Measured results

| Operation | Native successful helper | Current product, either helper mode |
| --- | --- | --- |
| stage | index blob contains CLEAN:updated | success, index blob contains WORK:updated |
| restore | file contains WORK:base | success, file contains CLEAN:base |
| merge | new commit/tree contains RESOLVED:ours+theirs | conflict; original HEAD/index/files restored |
| merge preview | clean candidate tree contains RESOLVED:ours+theirs | conflict on data.txt; no candidate tree exposed by API |

Outcomes agree in 8/16 pairs, but agreeing success does not imply equal contents.
Every native case invokes exactly one helper; product invokes none. Both object
formats produce the same semantic results. The saved state includes refs, HEAD,
HEAD tree/content, index entries with blob OIDs/content, worktree content, merge
state and ignored/untracked sentinels. Successful preview candidate tree and
content are recorded separately. Native failed merge-tree's raw conflict-tree
OID remains in stdout (`said`); it is not presented as a successful candidate.

Required clean refusal makes native add fail with the original index and file
preserved. Product still succeeds and stages untransformed worktree bytes.
Required smudge refusal makes native restore fail **after removing data.txt** in
these fixtures, while preserving the index and refs. Product succeeds with
canonical stored bytes. A future fail-closed implementation must account for
partial worktree mutation rather than infer that every error preserved files.

Native merge-helper refusal yields a conflict with unchanged HEAD and unresolved
index. The runner saves this intermediate state, then explicitly executes
reset --merge, matching the cleanup GitMutations.merge already performs. The
reported after-state is after cleanup for both engines. This census does not
claim native merge automatically rolls back conflicts. Product's intermediate
failure state is not captured because its public API performs cleanup internally.
Successful/failed preview leaves refs, index entries and files unchanged, but can
write objects; no object-database rollback or unreachable-object census is claimed.
Ignored and untracked sentinels survive all cases.

These differences agree with the current runner implementation: _neutral clears
discovered filter commands and sets required=false, while replacing custom merge
drivers with /usr/bin/false. They are pre-existing compatibility behavior, not a
new race exploit. The architecture's requirement that a future denied helper
must not silently stage unfiltered content remains unmet by current behavior.
No compatibility or security acceptance is inferred from evidence-integrity PASS.

## Validation and remaining work

verify.py checks matrix completeness, positive helper input/refusal controls,
content and mutation assertions, pair comparisons, OID widths and 287 SHA-256
source hashes: 279 product Python files, original SEC-11 race and host-exec test,
four SEC-13 Python artifacts, runner and verifier. Hashes are checked again at
runner completion and verifier time. Repeated verification is byte-identical;
Python syntax and staged whitespace checks also pass.

No product, existing tests/security assertions, policy, runtime routing or
approval mechanism changed. No daemon/service-journal reproduction, full core/UI,
coverage, performance, CI/install or independent host security gate was run.
Only ordinary populated repositories originate calls here. Linked/bare/unborn
callers, sequencer recovery message/index edits, consequential hook refusal
differences through daemon RPC and the remaining SEC-14 matrix are still needed.
Stage B still requires an isolated immutable installed runtime and unchanged
SEC-11/13 attack probes. Live-worktree publication remains unresolved. No
shell/interpreter exception for the ordinary Git worker is authorized.
