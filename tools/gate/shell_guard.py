"""Grok PreToolUse hook for shell calls: deny network, history rewrites and gate bypasses."""

from __future__ import annotations

import json
import re
import sys

# A program name as its own word, also when called by path (`/usr/bin/curl`).
_NETWORK = re.compile(r"(?<![\w.-])(?:curl|wget|nc|ssh|scp)(?![\w.-])")
# `git` with any global options (`-C dir`, `-c key=value`, `--git-dir=...`) before the subcommand.
_GIT = r"\bgit(?:\s+(?:-[Cc]\s+\S+|--[a-z-]+(?:=\S+)?))*\s+"
_GIT_REMOTE = re.compile(_GIT + r"(?:clone|fetch|pull|push)\b")
_GIT_REWRITE = re.compile(
    _GIT + r"(?:rebase|reset\s+--hard|commit\s+[^;&|]*--amend|push\s+[^;&|]*--force|filter-branch|replace|update-ref)\b"
)
# Branch moves of any kind: `checkout <branch>`, `checkout -- <file>` (overwrites files) and `switch`.
_GIT_MERGE = re.compile(_GIT + r"(?:merge|checkout|switch|worktree\s+(?:add|remove|prune))\b")
_PIP = re.compile(r"\b(?:pip3?|uv)\s+(?:install|add|remove|sync|lock|pip)\b")
# Running the gate script, not reading it: an interpreter, `./`, or the path as the command word.
_DIRECT_GATE = re.compile(r"(?:\bpython[\w.]*\s+(?:-\S+\s+)*|(?:^|[;&|(]\s*))\S*phase_gate\.py\b")
_RM = re.compile(r"(?<![\w.-])rm\s+([^;&|]*)")
_DEV_NULL = re.compile(r"[0-9]?>{1,2}\s*/dev/null|[0-9]?>&[0-9]")
_CONFIG = re.compile(r"(?:~|\$HOME|/home/[^/\s]+)/\.(?:config|grok|cursor)/")
_PROTECTED = (
    "tools/gate/",
    "tools/hostrun/",
    "tools/check.sh",
    ".grok/",
    "docs/plan/CORE.md",
    "docs/plan/FRONT.md",
    "docs/plan/PARALLEL.md",
    "docs/plan/ROUND6.md",
    "docs/plan/core/PHASE_PROTOCOL.md",
    "docs/plan/core/bases.json",
    "docs/plan/core/templates/",
)
# Commands that can write a file. An inline interpreter (`python -c`, `perl -e`) counts: it can open anything.
_WRITERS = re.compile(
    r"(?:>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\btruncate\b|\bchmod\b|\bln\b|\bpatch\b|\bgit\s+restore\b"
    r"|\b(?:python[\w.]*|perl|ruby|node)\s+(?:-\S+\s+)*-[ce]\b)"
)


def decision(command: str) -> str | None:
    if _NETWORK.search(command):
        return "network clients are not allowed"
    if _GIT_REMOTE.search(command):
        return "git clone/fetch/pull/push is not allowed"
    if _GIT_REWRITE.search(command):
        return "rewriting git history is not allowed"
    if _GIT_MERGE.search(command):
        return "merges, branch switches and worktree changes are done by the user"
    if _PIP.search(command):
        return "package installs are not allowed; dependencies change only through `hostrun deps-sync`"
    if _DIRECT_GATE.search(command):
        return "run the gate only as `hostrun gate <TASK>`"
    if _CONFIG.search(command):
        return "writing or reading agent and host configuration is not allowed"
    for match in _RM.finditer(command):
        words = match.group(1).split()
        short = [word for word in words if word.startswith("-") and not word.startswith("--")]
        recursive = "--recursive" in words or any("r" in word or "R" in word for word in short)
        targets = [word for word in words if not word.startswith("-")]
        if recursive and any(".aconsole-dev" not in target for target in targets):
            return "recursive rm is only allowed under .aconsole-dev"
    if _WRITERS.search(_DEV_NULL.sub(" ", command)):
        for prefix in _PROTECTED:
            if prefix in command:
                return f"protected path in a writing command: {prefix}"
    return None


def _command(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    tool_input = payload.get("toolInput", payload.get("tool_input", payload))
    if not isinstance(tool_input, dict):
        return ""
    raw = tool_input.get("command", "")
    return raw if isinstance(raw, str) else ""


def main() -> int:
    # Grok runs hooks fail-open: a crash would let the command through, so any error is a deny.
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = decision(_command(payload))
    except Exception as exc:  # noqa: BLE001 -- fail closed on any input
        reason = f"shell guard could not read the hook input: {type(exc).__name__}"
    if reason is None:
        sys.stdout.write(json.dumps({"decision": "allow"}) + "\n")
        return 0
    sys.stdout.write(json.dumps({"decision": "deny", "reason": reason}) + "\n")
    sys.stderr.write(reason + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
