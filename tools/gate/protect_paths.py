"""Grok PreToolUse hook: deny core-agent writes outside the core area.

Paths are resolved against the workspace root first, so `../x`, absolute
paths and symlinks that leave the worktree are denied, and every check runs
on the repository-relative form.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BLOCKED = (
    "tools/gate/",
    "tools/hostrun/",
    "tools/check.sh",
    ".grok/",
    ".cursor/",
    ".git/",
    "docs/plan/",
    "AGENTS.md",
    "src/aconsole/core/AGENTS.md",
    "src/aconsole/ui/views/",
    "src/aconsole/ui/viewmodels/",
    "src/aconsole/ui/style/",
    "src/aconsole/ui/resources/",
    "src/aconsole/ui/po/",
    "src/aconsole/ui/AGENTS.md",
    "tests/ui/",
    "tests/gui/front/",
)
ALLOWED = ("docs/plan/core/progress/",)
PATH_KEYS = ("file_path", "path", "target_file", "target_notebook", "notebook_path")


def decision(relative: str) -> str | None:
    for prefix in ALLOWED:
        if relative.startswith(prefix):
            return None
    for prefix in BLOCKED:
        if relative == prefix.rstrip("/") or relative.startswith(prefix):
            return f"protected path: {prefix} (core agent may not write here; ask the user)"
    return None


def relative_to_root(raw: str, root: Path, cwd: Path) -> str | None:
    """Repository-relative path, or None when the path leaves the root."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    resolved = Path(os.path.realpath(candidate))
    real_root = Path(os.path.realpath(root))
    try:
        return resolved.relative_to(real_root).as_posix()
    except ValueError:
        return None


def _paths(payload: dict[str, object]) -> list[str]:
    tool_input = payload.get("toolInput", payload.get("tool_input"))
    if not isinstance(tool_input, dict):
        return []
    found: list[str] = []
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    return found


def _deny(reason: str) -> int:
    sys.stdout.write(json.dumps({"decision": "deny", "reason": reason}) + "\n")
    sys.stderr.write(reason + "\n")
    return 2


def main() -> int:
    # Grok runs hooks fail-open: a crash (bad JSON, a NUL byte in a path) would let the edit through.
    try:
        return _check(json.loads(sys.stdin.read() or "{}"))
    except Exception as exc:  # noqa: BLE001 -- fail closed on any input
        return _deny(f"protect_paths could not check the hook input: {type(exc).__name__}")


def _check(payload: object) -> int:
    if not isinstance(payload, dict):
        return _deny("hook input is not a JSON object")
    root_text = payload.get("workspaceRoot") or payload.get("cwd") or str(Path.cwd())
    cwd_text = payload.get("cwd") or root_text
    if not isinstance(root_text, str) or not isinstance(cwd_text, str):
        return _deny("hook input has no workspace root")
    root = Path(root_text)
    cwd = Path(cwd_text)
    for raw in _paths(payload):
        relative = relative_to_root(raw, root, cwd)
        if relative is None:
            return _deny(f"path outside the worktree: {raw}")
        reason = decision(relative)
        if reason is not None:
            return _deny(reason)
    sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
