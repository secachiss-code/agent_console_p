# CORE-2 — approvals bypass a busy tab worker

Base: 9f7fcff on shared main. No dependencies, contracts, GUI views or installed applications were changed.

## Reproduction and outcome

A real HTTP PermissionRequest previously queued settings and a complete tab snapshot behind checkpoint Git work on the single tab worker. The approval card and decision could not complete until that work finished.

The new regression creates 1500 files (about 6 MB) in the actual tab workspace, verifies the captured tree contains them, and gates the first real capture for one second. It then posts a permission hook, receives ApprovalRequested through a subscribed IPC connection, decides DENY over IPC and waits for the HTTP hook response. The deterministic pause models slow Git/I/O; these numbers are contention measurements, not a claim about all repositories or a broad throughput benchmark.

| Measurement | Before | After | Bound |
| --- | ---: | ---: | ---: |
| HTTP request through IPC decision during capture | 1405.823 ms | 3.194 ms | 250 ms |
| Loop wakeup during a stalled real MCP Git lookup | 4.179 ms | 2.365 ms | 250 ms |
| HTTP request through IPC decision during setup checks | not measured in baseline | 4.748 ms | 250 ms |

The real Git capture duration is separately recorded in measurements.json (without the deterministic pause). Its duration is informational; the 250 ms assertions apply to approval responsiveness and loop wakeup, not to how fast a whole tree must be captured.

## Implementation

The daemon publishes the immutable Settings record after loading/updating it and keeps loop-owned tab/worktree paths current on registration, TabAdded, TabUpdated and TabRemoved. PermissionRequest reads that context directly instead of entering the busy tab queue or synchronously reading settings from SQLite. A settings update that fails to persist does not publish a new policy. Unknown/removed tabs, missing inline UI, native mode and shutdown keep their existing fallback behavior. Pending approval ownership and cleanup remain unchanged.

Tab mutations, checkpoint ordering and setup state still belong to their existing worker. This change removes approval handling's dependency on that worker; it does not move mutable tab state into concurrent worker pools or claim that every tab operation can run in parallel.

The MCP part of the brief was already fixed by the pinned mcp 2.3.0 SDK: its FuncMetadata.call_fn runs synchronous tools via anyio.to_thread.run_sync, preserving request context and using the SDK's bounded worker limiter/cancellation semantics. The baseline loop measurement already passed. The regression locks in this behavior, and an obsolete bootstrap comment plus publish documentation were corrected. No duplicate executor or new dependency was introduced.

## Validation

- Baseline new tests: one failing checkpoint-contention case, two passing MCP/policy cases. The measured approval delay exceeded the unchanged 250 ms threshold.
- Four final new cases cover checkpoint contention, setup contention, MCP loop responsiveness, and native-to-inline policy updates through public settings RPCs.
- `hostrun test core tests/core/test_core_latency.py tests/core/test_approval_hold.py tests/core/test_approval_tab_isolation.py tests/core/test_claude_mcp.py tests/core/test_mcp_tools.py tests/core/test_mcp_core4_http.py tests/core/test_review_wire.py tests/core/test_core_recovery.py`: exit 0, 53 tests.
- `hostrun test core`: exit 0, 1811 regular tests. See full.txt.
- `hostrun check`: exit 0. Ruff and strict basedpyright (0 errors/warnings), six architecture contracts, ten contract checks and API floor pass. Its known ownership substep emits Not a git repository and is not claimed as a validated ownership check.
- `git diff --check`: exit 0. Existing tests were not edited or weakened; the bound remained 250 ms.

The tests use real daemon HTTP/IPC, Git and SQLite with fake container plumbing, not a native model query. No installed daemon or user application was restarted. The independent SEC-11 race diagnostic remains FAILED and is not part of this node's green acceptance. No unsupported legacy CORE phase-gate PASS is claimed.
