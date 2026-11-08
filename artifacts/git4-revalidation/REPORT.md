# GIT-4 — acceptance revalidation

Existing implementation: a286858. No product code or existing test changed for this node.

Both Worktrees._remove and GitMutations.remove_worktree leave Git's final dirty/untracked check enabled. Ignored-data consent does not add --force; status.showUntrackedFiles=all is forced for the final remove command. The writer refuses ignored data without consent and only releases its exact runtime lock.

Fourteen cases in test_cleanup_discard_race.py cover both adapters: a controlled write immediately before remove (untracked, tracked, nested repository, and hidden-untracked configuration) must survive with a refusal; explicit ignored-only discard succeeds; lack of consent preserves ignored bytes; normal lock lifecycle allows clean removal. The submodule preservation regressions are included in the same full run. The guarantee covers these boundaries; it does not claim isolation from every external writer.

Validation: every named case is included in the current `hostrun test core`, exit 0, 1811 tests; raw output is in artifacts/core2/full.txt. The same source passes hostrun check with the known unvalidated ownership warning. The 53-case focused CORE-2 command is reported separately, not represented as covering these additional modules. No native GUI/CLI run, installed restart or unsupported legacy GIT-4 phase-gate PASS is claimed.
