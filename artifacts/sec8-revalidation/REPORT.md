# SEC-8 — original acceptance revalidation

Implementation: bf46375 / 273fbdc / 583fab0, with additional environment hardening recorded under SEC-11 in this continuation.

1. test_status_diff_and_snapshot_do_not_run_fsmonitor_or_hooks_path verifies status, diff and snapshots with marker programs in core.fsmonitor and a foreign core.hooksPath. Indirect include/worktree configuration is also covered.
2. test_only_the_runner_spawns_git scans all of src/aconsole/core; the new test_core_has_no_direct_git_spawn_including_async_and_other_runner_modules only exempts infrastructure/git/runner.py and also recognizes asynchronous subprocess calls. HostRepos and MCP git reads delegate to GitRunner.
3. Existing marker tests cover diff.external, textconv and core.sshCommand, in addition to fsmonitor/hooksPath. The newer filter/merge/signing/trailer tests and askpass/editor/PATH tests also run.
4. runner.py and operations.py document that explicit user commit/merge writes alone can run the repository's own hooks directory, never arbitrary core.hooksPath. The permission-boundary test rejects reads, other writes and command-prefix tricks. Tests verify own commit hooks and public-RPC refusal from merge hooks.

Verification: 71 focused tests and all 1807 regular core tests pass; hostrun check exits 0 with its known ownership warning. Raw logs are in artifacts/sec11.

This closes SEC-8's original fixed-key and explicit-hook criteria, not general confinement of every Git child process. The dynamically introduced driver race is separately reproduced and remains FAILED as SEC-11; see artifacts/sec11/REPORT.md. No universal security PASS or legacy SEC phase-gate PASS is claimed.
