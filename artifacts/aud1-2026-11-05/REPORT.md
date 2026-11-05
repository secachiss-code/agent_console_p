# AUD-3: Git and data-preservation audit

Audited main: `72612ff`. Parent plan task: AUD-1. Audit task: AUD-3.
Product source and existing tests were not changed by this audit. Twelve scenarios ran
against new Git repositories under a fresh `/tmp/aconsole-aud1-*` directory inside the
agent container. `results.json` records the actual output; `reproduce.py.txt` contains
the complete disposable-repository probe.

Run from the project root:

```sh
PYTHONPATH=src .venv-core/bin/python artifacts/aud1-2026-11-05/reproduce.py.txt
```

The probe creates temporary repositories, including disposable "outside" directories.
It never performs these destructive scenarios against the project checkout or real data.
Its outputs describe observed behavior, including defects; exit zero is not a safety verdict.

## Confirmed defects added to the AConsole plan

### REL-1: deletion follows a parent symlink outside the worktree (critical)

Source: `src/aconsole/core/infrastructure/git/checkpoints.py`,
`GitCheckpoints.materialize`; `src/aconsole/core/infrastructure/git/operations.py`,
`GitMutations.restore_file`.

1. Capture a checkpoint before `sub/victim.txt` exists.
2. Add and commit `sub/victim.txt` afterwards, so the live Git index still lists it.
3. Replace the working `sub` directory with a symlink to a separate temporary directory
   containing its own `victim.txt` sentinel.
4. Restore the earlier checkpoint through `CheckpointService.restore`.

Observed: the outside sentinel is deleted, while the API returns `restored=True`.
`materialize` enumerates the live index and then applies `Path.is_file()` / `Path.unlink()`
to `repo / path`, following a symlink in the parent path. The safety capture stores the
`sub` symlink, not the outside file's bytes, so it cannot recover that file.

A second disposable case invokes the single-file restore writer after taking its safety
ref, as `GitOpsService.restore_file` does. Restoring an absent source path through the
same parent symlink also deletes the outside sentinel and returns success. The service
validates the path lexically, but that does not prevent this parent-symlink traversal.

### REL-2: ignored `.aconsole/` contents disappear without explicit discard (high)

Source: `src/aconsole/core/infrastructure/git/worktrees.py`, `_status_ignored`,
`Worktrees.state`, `Worktrees.close`. The same status helper feeds GitMutations.has_ignored.

1. Track a `.gitignore` containing `.aconsole/` and create an app-managed worktree.
2. Release its app lock; add `.aconsole/notes.txt` containing private notes.
3. Git reports `!! .aconsole/`; ask for state, then close without `discard_ignored`.

Observed: state is `clean`; close returns `removed=True, reason='clean'`; the notes vanish.
The helper exempts the whole collapsed `.aconsole` directory, although only the lock is
safe to ignore. Git itself removes ignored files without requiring `--force`.

### REL-3: a dangling symlink remains after successful checkpoint restore (medium)

Source: `src/aconsole/core/infrastructure/git/checkpoints.py`, `GitCheckpoints.materialize`.

Capture a checkpoint, add an untracked symlink to a nonexistent target, then restore.
Observed: the API reports success, but `git status` still shows `?? dangling`.
`Path.is_file()` is false for a dangling symlink, so cleanup skips it. Fix together with
the no-follow deletion primitive from REL-1; REL-3 depends on REL-1 in the plan.

## Scenarios that preserved data

| Scenario | Observed result |
|---|---|
| Ordinary new file after checkpoint | Removed from worktree by restore, but its exact bytes are in the safety checkpoint. The original REL-22 allegation of no snapshot is not confirmed for ordinary files. |
| Dirty worktree close | Refused with `reason=dirty`; new file remains. |
| Ignored directory other than `.aconsole` | Kept with `reason=ignored` until explicit discard. |
| Explicit discard of ignored files | Worktree removed only after `discard_ignored=True` in this case. |
| Leading dash | Literal `--help` filename is staged as data; `base='--help'` is rejected before worktree creation. |
| Existing real `.git/index.lock` | Stage returns a refusal; the index and another owner's lock are unchanged. A checkpoint's private index still captures working content. |
| Conflicting merge with unrelated dirty content | Merge fails; HEAD, the original conflicting file and unrelated local edits remain. The writer aborts the failed merge; it does not leave an unresolved index. |
| Linked worktree with `.git` file | Checkpoint capture and restore succeed; main checkout stays unchanged. |

## Integration validation after promotion to main

All former local agent branches have zero commits missing from main. Main was advanced
without rewriting history to merge commit `72612ff`.

`hostrun check` initially failed because ignored generated contract types were stale.
Regenerated `docs/contracts/generated/types.md` from the current contract catalog.
The repeated `hostrun check` returned zero: Ruff, Basedpyright, import contracts,
contrast, all 10 contract checks and API-floor checks passed. Its ownership substep
still emits "Not a git repository" for the service's snapshot and does not propagate
that failure. This is the existing QA-4 ownership gap, not evidence that ownership was
validated successfully.

`hostrun test gui -k 'approval or clipboard or startup or b0 or b1'` stopped before test
collection with `isolated_x: no Wayland socket in XDG_RUNTIME_DIR`. GUI acceptance remains
unverified. No display configuration or hostrun policy was changed.

## Remaining scope

This is a completed read-only slice of AUD-1, not acceptance of the parent or the three
fixes. Full GUI/API restore flows, bare repositories/submodules, adversarial concurrent
filesystem changes and all rollback failure paths still need review. Product fixes
must retain their own tests and commits. Existing verifying nodes continue to reserve
Git and test paths; this audit has not cleared those reservations.
