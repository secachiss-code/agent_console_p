# SEC-17: reviewed Git command and process inventory

Base: `701bdbba97238b33b8a8317a94dbb301a3cb944c`.
Static review of current source, not a runtime command allowlist. No product
changes. SEC-11 remains failed; stage A is not declared complete.

## Reproduction and evidence

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/command-inventory/run.py
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/sec11/command-inventory/verify.py
```

inventory.json pins all 279 product Python files and the scanner. The AST scan
captures 119 records: eight runner constructions, 49 candidate runner calls,
38 reviewed wrapper calls, six internal runner transitions, four direct hook
discovery/execution calls, 13 standard-library process creation calls and one
library spawn candidate. It also records all eight direct runner import
statements; none uses an alias. Each call includes source path, line, containing
class/function, target, positional/keyword expressions, and a limited expansion
of module string/tuple constants. Unresolved expressions remain explicit JSON
objects. Names inside starred args/local variables are **not** resolved by this
scanner. These counts overlap semantic operations; 119 is not a command count.

The earlier SEC-15 scan used a file-text prefilter and missed clipboard's inline
GitRunner(...).read. This scan includes it. verify.py re-parses all sources and
requires exact equality with the saved scan, scanner hash equality, unchanged
source hashes and no aliased runner imports. It also checks that all old
candidate path/target multiplicities remain represented, ignoring line changes;
that check is not a whole-program equivalence proof.

## Reviewed command families and wrapper resolution

All paths below are relative to src/aconsole/core/infrastructure. Line numbers
refer to the pinned source. Arguments remain data needing operation-specific
validation; listing a command does not authorize arbitrary flags.

| Source / entry | Resolved commands and dynamic construction | Hook mode / boundary |
| --- | --- | --- |
| git/history.py:58 GitHistory | worktree list; for-each-ref; symbolic-ref; status; rev-list; merge-base; merge-tree --write-tree; diff; rev-parse; log; hash-object. log builds optional grep and passes tips on stdin; diff delegates through GitDiff. | All read, hooks disabled. merge-tree --write-tree can write objects even through a nominal read call; read is not filesystem read-only enforcement. |
| git/operations.py:45 stage/unstage/restore | add -A, restore --staged, rm --cached, restore --source; --literal-pathspecs constant precedes command, paths after --. Direct remove_worktree_file handles absent source entries. | write, native hooks disabled. Filters remain a separate helper/SEC-11 issue. |
| git/operations.py:53 commit; :103 merge | commit -F stdin; merge --ff-only, --no-ff or --squash; squash calls commit on success, failed merge calls reset --merge. | Only these four literal runner call sites set hooks=True. Runner checks first command commit/merge and write=True. |
| git/operations.py:137 with_trailers | interpret-trailers; trailer key/value pairs generate repeated --trailer arguments; message stdin. | read, hooks disabled; trailer command keys neutralized as defense in depth. |
| git/operations.py:145 merge_hooks | _hook selects common-dir/hooks/pre-commit or pre-merge-commit plus commit-msg; read-tree builds temporary index; write-tree checks squash index against preview. | Two _run_hook call sites bypass GitRunner, then one subprocess.run implementation. Environment copies os.environ, sets temp GIT_INDEX_FILE, drops only GIT_DIR/GIT_WORK_TREE here. SEC-16 fallback incompatibility remains. |
| git/operations.py:218 tree/ref | rev-parse; commit-tree with optional parent pairs and message stdin; update-ref with/without old OID; branch -D; worktree remove prefixed by -c status.showUntrackedFiles=all. | Native hooks disabled even for commit-tree/update-ref; no-checkout phases are not normal commit semantics. |
| git/operations.py:230 rebase/cherry_pick/step | rebase --no-autostash onto; cherry-pick oid; dynamic kind plus --action. application/services/git_rewrite.py:47, :147 constrains resumable kind to rebase/cherry-pick/merge and public action to continue/abort. | Native hooks disabled, including rewrite/continuation hook phases. This is source review, not measured compatibility or exclusion of internal callers with other values. |
| git/checkpoints.py:70 capture | rev-parse HEAD; absent HEAD uses hash-object -w -t tree --stdin; read-tree → add -A → update-index --force-remove lock → write-tree in private index; commit-tree → update-ref. Optional parents/local args remain expressions in JSON. | _stdout(:133) → runner.invoke(:140), write=True even for its query calls; no hooks. Private index and optional synthetic author environment; data writes remain real. |
| git/checkpoints.py:88 materialize/delete/paths | restore per snapshot path; ls-files; ls-tree; diff --numstat; update-ref -d. _z(:127) → _stdout(:133). | No hooks; restore/deletion of extra files also crosses ordinary filesystem code, not just Git execution. |
| git/diff.py:101 summary/file/compare | _stdout(:153) prepends diff --no-ext-diff --no-textconv to locally built diff flags/sides; _untracked(:159) directly calls _git_stdout with ls-files. Both reach _git_stdout(:204) → invoke(:207). | No hooks; Git's patch output is checked after capture. Untracked file reads are direct _read_regular filesystem reads. |
| git/worktrees.py:210 create/close/prune | _run(:442) strips leading -C/path. worktree add [-b/--detach], remove [--force only rollback], prune; branch -D; update-ref; status; rev-parse; rev-list. _roll_back(:235) loops over two tuples in its body; _remove(:313) assigns local remove tuple, explaining unresolved args/remove in AST. | No hooks. _run classifies write by first non-dash word except status/rev-parse/rev-list. -c value can become that heuristic word, so it is not a reliable policy parser. |
| tabs.py:326 HostRepos | toplevel(:333) → _ask(:340), rev-parse --show-toplevel and symbolic-ref --short HEAD → invoke(:344). | Read/default GitCall, no hooks. |
| mcp/tools.py:1532 _diff/_verified | _git(:1541) → shared _GIT_RUNNER.read(:1545), diff --stat with guards and rev-parse HEAD. | Read, no hooks. Variadic args retained; current two literal callers reviewed. |
| clipboard.py:64 _exclude | Inline GitRunner.read rev-parse --git-path info/exclude; then direct open/write of returned pathname. | Read Git call, no hooks; subsequent filesystem write needs its own boundary. O_NOFOLLOW only describes its leaf open, not parent-path confinement. |

## Runner internal transitions and helper surface

git/runner.py:129/:142 read/write → invoke(:156) → _spawn(:172) →
_execute(:197) → subprocess.run(:205). hooks=True first resolves common dir via
recursive _spawn(rev-parse), then every _spawn lists config driver keys in a
separate process before executing the requested argv. That separation retains
the original SEC-11 race. _config and _without_drivers provide overrides;
_guard_diff inserts flags for diff/show/log. No resolved config listing is a
security boundary against a concurrent change.

Binary selection is not runtime identity pinning: host_bins.py:13 searches fixed
system directories, then shutil.which, then a default path. The runner accepts
an injected absolute git path. It configures absolute true/cat/false helper
paths, suppresses native hooks except explicit commit/merge, and drops selected
executable environment variables. It still inherits other environment entries;
there is no filesystem/process/network sandbox here. _execute timeout targets
subprocess.run's child and captures output before checking size in read/diff;
this code does not establish a streamed cap or descendant cancellation.

Git implementation helpers and shell/interpreter descendants must be measured,
not inferred from command names. Rebase/worktree/status with submodules may
reach internal/external helpers; .gitattributes, config/includes, alternates,
linked gitdirs and the original race remain relevant across read/write calls.
These are required follow-up surfaces, not newly executed attacks in SEC-17.

## Other process entries: disposition of every recorded site

| Recorded source | Current role and scope implication |
| --- | --- |
| git/runner.py:205 | Ordinary Git subprocess boundary described above. |
| git/operations.py:211 | Direct repository hook argv. Must route together with GitRunner in stage E, using the separate broker; cannot remain a host bypass. |
| git/worktrees.py:421 | /bin/sh .aconsole/setup.sh after approved hash comparison. Separate repository-selected execution surface, with its own timeout process group. Check-then-path execution is not an immutable script bundle. No new race was executed here. |
| containers/podman.py:204 | Podman wrapper; builds inspect/start/exec operations. Commands inside containers may themselves run Git; this is not covered by routing the core GitRunner. |
| containers/distrobox_env.py:95 | Distrobox enter -d to discover environment. Separate process boundary. |
| agents/claude/limits.py:81 | Absolute CLI path, requires argv exactly claude auth status; not a core Git command. |
| agents/live_limits.py:69 | Short-lived app-server argv for limits/auth exchange. Separate process tree. |
| codegraph/runner.py:80 | hostrun devtools language path; separate named host execution authority. |
| setup_checks.py:50 | Setup environment command wrapper, used for Podman and container probes. Not a GitRunner adapter. |
| devcli/mcp_conformance.py:43 | Development conformance command process, outside core Git operations. |
| sidecar/main.py:75/:86 | Two exec transitions for agent/bridge argv. Agent-run Git belongs to the container boundary, not this core worker. |
| ui/client/core_client.py:210 | Daemon startup argv. No UI mutation made. |
| ui/views/terminal/session.py:147 | VTE spawn_async of launch spec argv/env/cwd, recorded separately as library candidate. This is another path to agent process execution, not a GitRunner caller. |

## Remaining gate work

This review resolves the current named wrappers and local command families for
review. It does not recursively validate every caller's data or every library
spawn, nor discover arbitrary reflection/import aliasing, monkeypatches, plugins,
C extensions, installed wrappers, or shell helper contents. Fixed spellings in
PROCESS_CALLS are deliberate search limits. New aliases/receivers can evade this
scanner and require a fresh audit. Tooling/dependencies outside src are not
scanned. All assertions validate recorded evidence, not confinement.

Next stage-A work: actual differential traces for rebase/cherry-pick/continue/
abort, checkpoint and worktree operations; positive required-helper controls;
linked/bare/unborn/SHA256 coverage; final-target-only transaction refusal and
individual post-hook failures; RPC reproduction of consequential no-checkout
discrepancies. Stage B then still needs the unchanged security probes and the
installed immutable launcher/runtime architecture from SEC-14. No product route,
shell exception or stage-completion claim follows from this static inventory.

Validation: scan re-computation, 279 source hashes, old candidate multiplicities,
runner syntax and whitespace. No product tests, coverage, CI/install, performance
or independent security gate were run or claimed for these artifact-only edits.
