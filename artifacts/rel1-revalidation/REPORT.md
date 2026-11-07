# REL-1 — acceptance revalidation

Existing implementation: f410c5e; no product changes were needed for this node.

Descriptor-relative removal opens the worktree and every parent with O_DIRECTORY/O_NOFOLLOW, then unlinks through the held directory FD. Checkpoint materialize and restore_file both use remove_worktree_file and wrap failures in diagnostic checkpoint/Git exceptions.

Six cases in test_restore_boundaries.py cover both entry points: an outside parent symlink is refused without changing the external sentinel; a controlled concurrent rename/symlink substitution cannot redirect an already-open directory; deleting a leaf symlink preserves its target. Existing checkpoint and public restoreFile tests cover normal restore, HEAD/index and safety behavior.

Validation: all relevant cases are included in `hostrun test core`, exit 0, 1807 tests; see artifacts/sec11/full.txt. The same source also passes hostrun check (known ownership warning is not a validated ownership check). The 71-test focused Git/MCP run is separately documented, without claiming it includes every case named here. Existing tests were neither edited nor skipped.

No legacy REL-1 phase-gate PASS is claimed. Acceptance uses the stated regression/integration evidence; installed application activation remains a separate task.
