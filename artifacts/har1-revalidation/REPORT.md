# HAR-1 — Claude MCP launch revalidation

Existing implementation: 0a6a207, with credential hardening in d9ba7cc. No launcher changes were required.

## Acceptance

- ClaudeLauncher writes a private mcp.json and supplies `claude --settings <settings> --mcp-config <mcp.json>` as the final launch argv. Its MCP URL is the daemon's loopback /mcp; authorization expands ACONSOLE_TAB_TOKEN from the launch environment. The token is absent from argv and configuration bytes; files have mode 0600, and destination symlinks are refused.
- test_claude_launch_config_lists_tools_and_isolates_projects opens tabs through a live daemon's IPC, obtains actual TerminalLaunchSpec argv/env, reads the generated config, initializes real HTTP MCP sessions, lists all twelve aconsole tools, creates and reads its plan, and receives PLAN_NOT_FOUND from the other project's token.
- test_mcp_tools_schema_content_and_hint asserts exactly twelve tools. The CORE-4 HTTP regression additionally checks independent tabs of one project and their leases/handovers.

## Evidence

- `hostrun test core ... tests/core/test_claude_mcp.py` with the eight CORE-4/MCP/persistence test files listed in artifacts/core4/REPORT.md: exit 0, 56 tests.
- `hostrun test core`: exit 0, 1794 tests. See artifacts/core4/full.txt.
- `hostrun check`: exit 0; the known ownership warning is documented in artifacts/core4/REPORT.md.
- Installed `/opt/aconsole/core/lib/python3.15t/site-packages/aconsole/core/infrastructure/agents/claude/launcher.py` matches main byte for byte. SHA256: 5f5ea8e5923421103df86406bd576c409e60207edfc4517a0b78ffa3869e38d7.

The live integration uses generated launch configuration and the real HTTP endpoint with fake container plumbing. It does not invoke a paid Claude model query or claim a new native CLI run. Acceptance specifically requires the final argv/config test and a tool call in the tab context; both are exercised. No legacy HAR phase-gate PASS is claimed.
