# GIT-3 — acceptance revalidation

Existing implementation: 2f3aa87. No product code or existing test changed for this node.

GitHistory.status and managed worktree status explicitly pass --ignore-submodules=none and --untracked-files=all. Repository submodule.*.ignore and diff.ignoreSubmodules cannot hide dirty/untracked submodule work from these safety checks.

Six cases in test_submodule_cleanup_safety.py use only local temporary repositories: four variants cover tracked changes, untracked changes and the relevant ignore settings; two exercise public git/cleanupWorktree with discard_ignored=True. Both adapters refuse deletion and preserve the exact unsaved bytes and branch. No network submodule fetch is used.

Validation: every named case is included in the current `hostrun test core`, exit 0, 1811 tests; raw output is in artifacts/core2/full.txt. The same source passes hostrun check with the known unvalidated ownership warning. The 53-case focused CORE-2 command is reported separately, not represented as covering these additional modules. No native GUI/CLI run, installed restart or unsupported legacy GIT-3 phase-gate PASS is claimed.
