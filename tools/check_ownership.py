"""Check ownership on a real Git diff; missing repository/baseline is an error."""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

_UI = (
    "src/aconsole/ui/viewmodels/*",
    "src/aconsole/ui/views/*",
    "src/aconsole/ui/style/*",
    "src/aconsole/ui/resources/*",
    "src/aconsole/ui/po/*",
    "src/aconsole/ui/AGENTS.md",
    "src/aconsole/__main__.py",
    "tests/ui/*",
    "tests/gui/front/*",
    "docs/ui/B[0-9]/*",
    "docs/ui/B[0-9][0-9]/*",
    "stubs/*",
    "tools/ui_snapshot.py",
    "docs/plan/front/execplans/*",
    "docs/contracts/requests/*",
)


def check(repo: Path, container: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "main...HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [f"ownership: cannot compare main...HEAD: {result.stderr.strip()}"]
    problems = []
    for path in filter(None, result.stdout.split("\0")):
        ui = path != "tests/ui/test_spike.py" and any(fnmatch.fnmatchcase(path, pattern) for pattern in _UI)
        if (container == "agent-codex") != ui:
            problems.append(f"ownership: {path}")
    return problems


def main(argv: list[str]) -> int:
    problems = check(Path(argv[0]), argv[1])
    for problem in problems:
        sys.stderr.write(problem + "\n")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
