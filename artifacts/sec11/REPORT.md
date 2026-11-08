# SEC-11 — environment hardening; config race still FAILED

Base: 951dc9f on shared main. Existing driver neutralization and consolidated callers are in 273fbdc; explicit hook permission is in 583fab0.

## Changed

Thirteen new regular regression cases verify repository askpass/editor settings, inherited and per-call executable environment variables, neutral-program PATH substitution, and direct Git spawn scanning across the entire core (including asynchronous spawn names and runner modules outside the one authorized implementation).

Before the fix six cases executed the marker program: inherited GIT_ASKPASS and GIT_SEQUENCE_EDITOR, per-call GIT_ASKPASS/GIT_EDITOR/GIT_SEQUENCE_EDITOR, and a fake true on PATH. GitRunner now merges per-call options first, strips executable environment overrides afterward, and enforces editor/pager/prompt settings last. Neutral true, false and cat programs use absolute host paths. Explicitly approved repository commit/merge hooks retain their existing behavior; existing tests remain unchanged.

## Checks

- `hostrun test core tests/core/test_git_security_environment.py tests/core/test_git_host_exec.py tests/core/test_git_operations.py tests/core/test_git_merge.py tests/core/test_checkpoints_git.py tests/core/test_git_wire.py tests/core/test_mcp_tools.py tests/core/test_claude_mcp.py`: exit 0, 71 tests.
- `hostrun test core`: exit 0, 1807 tests (regular suite). This command does not collect the standalone diagnostic under artifacts. See full.txt.
- `hostrun check`: exit 0 for product source/new regular tests. Ruff, strict types, six architecture contracts, ten contract checks and API floor passed. The known ownership substep emits Not a git repository; ownership validation is not claimed.
- Ruff also passes for the standalone diagnostic. Its sole SLF001 exemption accesses the private spawn boundary to schedule the race deterministically; there are no new product typing exemptions.

## Security acceptance remains red

The earlier reconciliation explicitly requires closing the mutable-config race, not only scanning known driver names. `hostrun test core artifacts/sec11/race_probe.py` exits 1: after the initial config enumeration, the diagnostic adds filter.late.clean, then the actual status command executes that new program. The marker is confined to a temporary repository. No user repository is attacked.

Static configuration and environment regressions passing does not satisfy that stronger requirement. SEC-11 stays failed with the reproducer and race.txt attached. The separate diagnostic keeps its failing security assertion, with no skip/xfail or replacement by a success assertion. It is intentionally not presented as a regular-suite security PASS.

Next implementation must prevent execution of programs introduced after enumeration while preserving explicitly approved commit/merge hooks. Adding more names, taking another mutable read, or detecting changes after execution is not a sufficient fix. The current runner is not a complete execution sandbox. No package deployment was performed.

No legacy SEC phase-gate PASS is claimed; legacy phase IDs do not include these MCP node keys.
