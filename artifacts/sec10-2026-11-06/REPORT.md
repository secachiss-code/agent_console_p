# SEC-10 — launch and HTTP admission hardening

Date: 2026-11-06. Baseline: b73d4b2. MCP plan-1, node-24.

## Findings reproduced

43 initial security regressions failed against the baseline. They expose the tab
bearer in podman exec arguments, accept option-like/unsafe session identifiers,
write through a symlink in HOME ancestors, admit foreign or ambiguous MCP
Origin/Host headers, and expand bearer headers into curl arguments. The initial
Podman test constructor was corrected before the recorded RED execution.

The Python sidecar already used -I before this node; that includes safe module
lookup and remains protected. The one-shot sidecar token already travelled only
in the launch environment. Both invariants now have public launchSpec coverage.

## Implementation

- Podman uses -e ACONSOLE_TAB_TOKEN without its value: Podman inherits the value
  from the sidecar launch environment. The public launchSpec preserves the bearer
  and one-shot sidecar credentials in env and excludes them from argv for Claude,
  Codex, OpenCode and Cursor.
- Generated Claude statusline curl reads the Authorization header on stdin while
  its JSON payload comes from the existing temporary payload copy. Codex hook and
  notify curl read Authorization through FD 3 while their payload remains on
  stdin. No expanded secret is placed in curl argv, settings or shell source.
  Both notify input modes are covered with real /usr/bin/curl and a local HTTP
  server, plus a wrapper recording argv without altering header/body handling.
- Runtime session IDs must begin with an ASCII letter/digit and contain only
  letters, digits, underscores or hyphens, at most 128 characters. Unsafe values
  fail with SESSION_NOT_FOUND before execution; telemetry no longer records them.
  Codex positional resume/fork IDs follow -- in both command builders.
  Claude --resume, Cursor --resume and OpenCode -s consume named option values:
  they retain their CLI grammar and use validated non-option values. Inserting
  -- between such an option and its required value would change the CLI meaning;
  the terminator applies to positional Codex IDs, not those named option values.
- Launcher HOME paths are walked from / with O_DIRECTORY|O_NOFOLLOW. Directory
  creation/chmod, Cursor config reads, temporary writes, fsync, atomic replacement
  and Claude tab-directory cleanup use anchored directory descriptors. A symlink
  ancestor is refused; a controlled parent substitution at replace cannot redirect
  bytes into the outside target. This addresses symlink traversal, not hostile
  bind mounts or arbitrary directory relocation by a privileged user.
- MCP checks Origin and Host before token resolution, including duplicate
  Origin/Host refusal. MCP and ingest share the loopback host allow-list and
  malformed authority rejection. The existing absent/null/local http(s) Origin
  policy is retained. Absent Host remains the HTTP parser's responsibility.

The three expected generated-script fixtures were updated explicitly for the
new header transport. User explicitly authorized on 2026-11-06 the one-line test_codex_turns.py
expectation update from resume ID to resume -- ID. Session identity assertions
remain intact; no test was skipped or marked xfail.
There are no UI view/layout changes, user-app restarts or new dependencies.

## Validation

All commands run through hostrun with
HOSTRUN_CWD=/home/sas/Projects/sp_aconsole_prototype.

- Targeted launcher/container/session/MCP/ingest suite: exit 0, 213 cases.
- Four public tab/launchSpec agent cases verify both credentials stay out of argv.
- Four live daemon HTTP cases verify foreign Origin/Host refusal on MCP and ingest.
- Real curl tests verify bearer and payload bytes for hook, both notify modes and
  statusline; captured curl argv contains no test credential.
- Descriptor-relative write test swaps the parent for a symlink at replacement;
  the outside sentinel stays unchanged and bytes reach only the anchored directory.
- hostrun test contracts: exit 0.
- hostrun check: exit 0; Ruff, strict types, architecture and contract checks pass.
  The known ownership substep emits Not a git repository and does not establish
  ownership validation.
- Full hostrun test core: exit 1, exactly one outdated expectation in
  test_codex_turns.py:91 requires `resume ID` rather than the required
  `resume -- ID`. All other core cases passed. The one-line update in proposed-test-update.diff was explicitly approved
  by the user and has now been applied; validation follows below.
- git diff --check: clean.

New type escapes are limited to the untyped ASGI headers list/pair boundary in
mcp/server.py. The new S310 suppression is limited to a fixed loopback HTTP test.
Native agent CLIs and a real Podman container were not started by this validation;
public daemon tests use the existing fake container adapter with temporary data.

Implementation is ready for human verification after the explicitly authorized
one-line expectation update. No successful final full-suite result is claimed.

## Final validation after user authorization

- The user explicitly approved updating the existing resume expectation and
  requested a commit on 2026-11-06. The session-identity assertion is unchanged.
- Approved Codex/session/security recheck: exit 0, 67 cases.
- Final full core: exit 1 with exactly one different timing failure,
  test_persistence_b_tick: max 6.125119 ms against 5 ms, p50 0.00447 ms and
  p99 0.01031 ms. The resume expectation passes and all other core cases pass.
  This tick test passed in the earlier full run.
- Separate persistence/security/Codex recheck: exit 0, 67 cases, including tick.
- PERF-2 (node-125) records this unresolved timing observation independently,
  without attributing its cause to SEC-10 or weakening its threshold.
- Production code is unchanged since the passing 213-case targeted suite and
  hostrun check. Contract tests also pass. Final commit includes these limits.
 SEC-11 still cannot
be claimed because UX-7 reserves an overlapping path; its Git execution risk is
independent of these launch/HTTP changes.
