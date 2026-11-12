# QA-18: release unscheduled UI coroutines

Base: `0319563ef7311a5c420ae4c8359bc25593c15271`.

`spawn_task` receives an already-created coroutine. If running-loop lookup or
task creation raises, no task takes ownership of it. Previously its frame stayed
open until collection and could produce `coroutine was never awaited`, as noted
for health refresh and Git watch work in QA-17. Close the coroutine on scheduling
failure and re-raise the scheduling exception. Successfully scheduled tasks retain
the existing strong-reference and completion-callback lifecycle.

## Regression evidence

On the unchanged base implementation, the new tests yielded **2 failed, 2 passed**:
both absent-loop and rejecting-task-factory cases observed `CORO_CREATED` instead
of `CORO_CLOSED`. The test finally blocks close leaked work even on that baseline.
The factory case also checks exception object identity and an unchanged task set.
Success and cancellation cases exercise task execution and reference release.

After the fix, the same four cases pass with `-W error::RuntimeWarning`.
The PyGObject asyncio policy deprecation warning remains visible.

## Validation and limits

Validation results and exact commands are recorded in `validation.json`.
The first strict type check could not resolve gi because the worktree has no
`.venv-ui`. Repeating with the existing project's `--venvpath` succeeds; the
source and type-checking strictness are unchanged.

The first broader client run in the sandbox produced 4 passed, 2 failed and
2 errors: all four client cases failed to start FakeCore because socket bind
returned `PermissionError: Operation not permitted`. This is retained as an
environment failure, not counted as a product regression or successful test.

The approved repeat outside the sandbox passed **8 tests in 0.93 s**, including
the real GLib application loop connecting to FakeCore. `RuntimeWarning` was
treated as an error. The only warning was the PyGObject policy deprecation.

This focused change does not establish that all QA-17 warnings are eliminated.
No full GUI/core/coverage/CI/install/performance or independent security
acceptance is claimed. QA-4 and PERF-7 remain failed; SEC-34 still awaits actual
independent review before Stage B. No view, style or layout changed.
