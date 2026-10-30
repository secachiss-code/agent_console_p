# Codex 0.164.0 live probes

Captured 2026-10-30 from the real `codex app-server` 0.164.0 (CODEX_HOME of agent-codex) by the A4.1 escalation script, extended with the launcher's `-c hooks.*` flags. Do not edit these files by hand; capture them again for a new Codex version.

- `initialize.jsonl`, `hooks-list.jsonl`, `rate-limits.jsonl`, `thread-new.jsonl`, `thread-clear.jsonl`: the response line for request ids 1–5. Notifications are dropped; `accountId` and reset-credit ids are redacted.
- `hooks-list.jsonl`: the seven aconsole hooks passed with `-c hooks.<Event>=...` and command `/home/sas/.containers/containers/agent-codex/.aconsole/bin/aconsole-hook codex`. All untrusted.
- `hooks-list-trusted.jsonl`: the same launch plus `-c hooks.state={ "<key>" = { trusted_hash = "<currentHash>" }, ... }` built from `hooks-list.jsonl`. All trusted.
- `state_5.schema.sql`: the real schema of `~/.codex/state_5.sqlite`. `state_5.sqlite`: that schema with one thread row (the probe thread).
- `rollout.jsonl`: structural records of a real rollout (session_meta, task_started, token_usage_record, two token_count, task_complete). Message bodies are removed, creator ids redacted, cwd replaced with `/tmp/aconsole-a41`.

Facts the probes show:
1. A hook passed with `-c` gets the key `/<session-flags>/config.toml:<snake_event>:0:0`; `eventName` is camelCase (`permissionRequest`).
2. Every hook has its own `currentHash`; it changes with the command and the timeout.
3. Codex clamps the SessionEnd timeout to 3 s and reports a warning.
4. `hooks.state` with these keys and hashes makes the hooks trusted.
5. The `thread/start` response does not echo `sessionStartSource`; `/new` and `/clear` responses differ only in ids.
6. In rollout `token_count`, `rate_limits.*.used_percent` is a float (91.0) and the record carries `limit_id`, `credits`, `plan_type`.
