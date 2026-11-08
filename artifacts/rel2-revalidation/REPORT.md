# REL-2 — acceptance revalidation

Existing implementation: 3d18f08; no product changes were needed for this node.

Only the exact .aconsole/lock entry is disposable. A collapsed ignored .aconsole directory is not assumed to contain only runtime data; normal status asks for individual untracked paths.

test_ignored_aconsole_data_requires_explicit_discard covers notes.txt and nested clipboard/image.png with .gitignore=.aconsole/: state is ignored, both adapters recognize user data, close without discard preserves bytes/worktree, explicit discard removes it. test_an_ignored_runtime_lock_alone_is_not_user_data verifies clean state, lock refusal, and removal after release. These tests also exercise the later scoped-discard safety implementation.

Validation: all relevant cases are included in `hostrun test core`, exit 0, 1807 tests; see artifacts/sec11/full.txt. The same source also passes hostrun check (known ownership warning is not a validated ownership check). The 71-test focused Git/MCP run is separately documented, without claiming it includes every case named here. Existing tests were neither edited nor skipped.

No legacy REL-2 phase-gate PASS is claimed. Acceptance uses the stated regression/integration evidence; installed application activation remains a separate task.
