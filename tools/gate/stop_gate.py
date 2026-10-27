"""Grok Stop hook: a turn may end only when `hostrun gate <TASK>` passes.

The current task is the first line of `.aconsole-dev/current-task`. A task
whose progress file says `Status: blocked` may stop: blocking with a reason is
the protocol's honest exit. Grok itself ends the loop after 8 continuations.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
_TASK = re.compile(r"^[A-Z][0-9]{1,2}(?:\.[0-9]{1,2})?[a-z]?$")
_TAIL = 40


def current_task(repo: Path) -> str | None:
    marker = repo / ".aconsole-dev" / "current-task"
    if not marker.is_file():
        return None
    lines = marker.read_text(encoding="utf-8").splitlines()
    if not lines or _TASK.match(lines[0].strip()) is None:
        return None
    return lines[0].strip()


def is_blocked(repo: Path, task: str) -> bool:
    progress = repo / "docs" / "plan" / "core" / "progress" / f"{task}.md"
    if not progress.is_file():
        return False
    for line in progress.read_text(encoding="utf-8").splitlines():
        if line.startswith("Status:"):
            return line.removeprefix("Status:").strip().startswith("blocked")
    return False


def gate_command(task: str) -> list[str]:
    hostrun = shutil.which("hostrun")
    if hostrun is None:
        return []
    return [hostrun, "gate", task]


def _allow() -> int:
    return 0


def _block(reason: str) -> int:
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")
    return 0


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        return _allow()
    end_reason = payload.get("reason")
    if isinstance(end_reason, str) and end_reason != "end_turn":
        return _allow()
    root_text = payload.get("workspaceRoot")
    repo = Path(root_text) if isinstance(root_text, str) and root_text else REPO
    task = current_task(repo)
    if task is None or is_blocked(repo, task):
        return _allow()
    argv = gate_command(task)
    if not argv:
        return _block("hostrun is not on PATH, so the gate cannot run. Mark the task blocked with this error.")
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=repo, timeout=1500)
    except subprocess.TimeoutExpired:
        return _block(f"`hostrun gate {task}` did not finish in 1500 s. Mark the task blocked with this error.")
    if completed.returncode == 0:
        return _allow()
    tail = "\n".join((completed.stdout + completed.stderr).strip().splitlines()[-_TAIL:])
    return _block(
        f"`hostrun gate {task}` exited {completed.returncode}. Fix what it reports, or mark the task "
        f"blocked in its progress file with the exact error.\n{tail}"
    )


if __name__ == "__main__":
    sys.exit(main())
