"""Summarize core progress files as JSON. The standard library only.

Each file is one task. The id is the filename. Status, the checklist under ``## Done when``,
``Unblock``, the commit, and the base are the fields ``tools/gate/phase_gate.py`` already reads.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

_ITEM = re.compile(r"^- \[( |x|X)\](.*)$")
_STATUS = re.compile(r"^Status:[ \t]*(.*)$", re.MULTILINE)
_UNBLOCK = re.compile(r"^Unblock:[ \t]*(.*)$", re.MULTILINE)
_COMMIT = re.compile(r"^Commit:[ \t]*(.*)$", re.MULTILINE)
_BASE = re.compile(r"^Base commit:[ \t]*(.*)$", re.MULTILINE)
_TITLE = re.compile(r"^#[ \t]+\S+[ \t]+[—-][ \t]+(.*)$", re.MULTILINE)
_WORD = re.compile(r"[A-Za-z]+")
_DONE_WHEN = "## Done when"


class Checklist(TypedDict):
    open: int
    ticked: int


class TaskState(TypedDict):
    base: str | None
    checklist: Checklist
    commit: str | None
    id: str
    status: str
    status_detail: str | None
    title: str | None
    unblock: str | None


class StateFile(TypedDict):
    tasks: list[TaskState]


def collect(progress: Path) -> StateFile:
    """Read every ``*.md`` in ``progress``. Tasks are ordered by id."""
    tasks = [parse_progress(path) for path in sorted(progress.glob("*.md"))]
    return StateFile(tasks=tasks)


def parse_progress(path: Path) -> TaskState:
    """One progress file. A missing field stays null rather than being invented."""
    text = path.read_text(encoding="utf-8")
    status, detail = _status(_field(_STATUS, text))
    return TaskState(
        base=_field(_BASE, text),
        checklist=_checklist(text),
        commit=_field(_COMMIT, text),
        id=path.stem,
        status=status,
        status_detail=detail,
        title=_field(_TITLE, text),
        unblock=_field(_UNBLOCK, text),
    )


def render(state: StateFile) -> str:
    """Stable JSON: sorted keys, real characters, one trailing newline."""
    return json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("progress", type=Path, help="directory of <ID>.md progress files")
    args = parser.parse_args(argv)
    directory = Path(str(args.progress))
    if not directory.is_dir():
        _ = sys.stderr.write(f"not a directory: {directory}\n")
        return 2
    sys.stdout.write(render(collect(directory)))
    return 0


def _field(pattern: re.Pattern[str], text: str) -> str | None:
    found = pattern.search(text)
    if found is None:
        return None
    value = found.group(1).strip()
    return value or None


def _status(raw: str | None) -> tuple[str, str | None]:
    if raw is None:
        return "missing", None
    text = raw.strip("*").strip()
    word = _WORD.search(text)
    status = "missing" if word is None else word.group(0).lower()
    detail = text.split(":", 1)[1].strip() if ":" in text else None
    return status, detail or None


def _checklist(text: str) -> Checklist:
    open_items = 0
    ticked = 0
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == _DONE_WHEN
            continue
        if not inside:
            continue
        found = _ITEM.match(line.strip())
        if found is None:
            continue
        if found.group(1) == " ":
            open_items += 1
        else:
            ticked += 1
    return Checklist(open=open_items, ticked=ticked)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
