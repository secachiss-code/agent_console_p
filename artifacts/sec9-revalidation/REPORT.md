# SEC-9 — acceptance revalidation

Source: main at 5c966ce; implementation: 0b1f778.

- `hostrun test core tests/core/domain/test_risk.py tests/core/domain/test_approvals.py`: exit 0, 290 cases.
- `hostrun check`: exit 0. Ruff, basedpyright, six architecture contracts, ten contract checks and API floor passed. The ownership substep printed “Not a git repository”; it is not evidence of ownership validation.
- The previous complete core run at the same implementation passed 1779 tests (see artifacts/sec5/REPORT.md). No product code changed for this revalidation.

## Acceptance

1. The A9 table in test_risk.py enumerates all eleven commands from the brief and asserts destructive tags, plus outside_worktree for home deletion.
2. Malformed quotes, substitutions and excessive nesting fail closed with every existing RiskTag; classifier remains advisory.
3. Writes to .git, .claude, .codex, executable Git settings and nested agent approval bypasses receive the existing destructive tag. No new contract enum was introduced.
4. test_tab_rule_does_not_cover_command_substitution checks both exact and prefix matches, assignment substitution, and ApprovalBook replay. prefix_matches rejects command substitution before exact equality.

The legacy phase gate does not accept SEC task IDs; no legacy gate PASS is claimed. MCP acceptance is supported by the checks above.
