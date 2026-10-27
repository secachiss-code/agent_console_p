"""Check a task's progress file against its brief: "Готово, когда" is copied, not rewritten.

The brief is read from `docs/plan/CORE.md` at the task base, so an edit made during the task does not count.
Rules: every `Done when` item is a verbatim piece of the brief's "Готово, когда" text (whitespace aside); every
backticked command of that text appears in some item; `Status: done` only when every item is `[x]`. Tasks from before
the rule (A0-A2) get no exemption: a reopened task starts over with a verbatim copy (ROUND6 K1.5).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

PROGRESS_DIR = "docs/plan/core/progress"
BRIEFS = "docs/plan/CORE.md"
_DONE_WHEN = "**Готово, когда"
_STOP = ("**Запрещено", "**Зависит от", "**Файлы", "**Шаги")
_ITEM = re.compile(r"^- \[( |x|X)\](.*)$")
_STATUS = re.compile(r"^Status:\s*(.*)$", re.MULTILINE)
_WORD = re.compile(r"[a-z]+")
# A verbatim item is a whole requirement, not a fragment such as "0" or "→ 0".
_SHORTEST = 12
_COMMAND = re.compile(r"`([^`]+)`")
_SPACE = re.compile(r"\s+")


class Hit(TypedDict):
    rule: str
    path: str
    detail: str


def _normal(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def brief_done_when(briefs: str, phase: str) -> str | None:
    """The "Готово, когда" text of the paragraph that starts with `**<phase> ·`, or None."""
    for paragraph in briefs.split("\n\n"):
        if not paragraph.lstrip("- ").startswith(f"**{phase} ·"):
            continue
        start = paragraph.find(_DONE_WHEN)
        if start < 0:
            return None
        text = paragraph[start + len(_DONE_WHEN) :]
        text = text.removeprefix(":**").removeprefix("**:").lstrip(":* ")
        ends = [found for marker in _STOP if (found := text.find(marker)) >= 0]
        return _normal(text[: min(ends)] if ends else text)
    return None


def progress_items(progress: str) -> list[tuple[bool, str]]:
    """`(ticked, text)` for each checklist line under `## Done when`."""
    items: list[tuple[bool, str]] = []
    inside = False
    for line in progress.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## Done when"
            continue
        if inside and (found := _ITEM.match(line.strip())) is not None:
            items.append((found.group(1) != " ", _normal(found.group(2))))
    return items


def _is_done(progress: str) -> bool:
    """`Status: done`, also written as `Done`, `**done**`, `done.` or `done (note)`."""
    status = _STATUS.search(progress)
    if status is None:
        return False
    word = _WORD.search(status.group(1).casefold())
    return word is not None and word.group(0) == "done"


def check(progress: str, briefs: str | None, phase: str) -> list[Hit]:
    path = f"{PROGRESS_DIR}/{phase}.md"
    hits: list[Hit] = []
    items = progress_items(progress)
    if _is_done(progress) and not all(ticked for ticked, _ in items):
        hits.append(Hit(rule="done-with-open-items", path=path, detail="Status: done while a Done when item is [ ]"))
    brief = None if briefs is None else brief_done_when(briefs, phase)
    if brief is None:
        hits.append(Hit(rule="brief-not-found", path=BRIEFS, detail=f"no '**{phase} ·' brief with 'Готово, когда'"))
        return hits
    if not items:
        hits.append(Hit(rule="done-when-empty", path=path, detail="no '- [ ]' items under '## Done when'"))
        return hits
    hits.extend(
        Hit(rule="done-when-rewritten", path=path, detail=text[:200] or "(empty item)")
        for _, text in items
        if len(text) < _SHORTEST or text not in brief
    )
    joined = " ".join(text for _, text in items)
    hits.extend(
        Hit(rule="done-when-dropped", path=path, detail=f"`{command}`")
        for command in _COMMAND.findall(brief)
        if f"`{command}`" not in joined
    )
    return hits


def _show(repo: str, base: str, relative: str) -> str | None:
    shown = subprocess.run(
        ["git", "-C", repo, "show", f"{base}:{relative}"], capture_output=True, text=True, check=False
    )
    return shown.stdout if shown.returncode == 0 else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo", required=True)
    _ = parser.add_argument("--base", required=True)
    _ = parser.add_argument("--phase", required=True)
    args = parser.parse_args(argv)
    repo = Path(str(args.repo))
    phase = str(args.phase)
    progress_path = repo / PROGRESS_DIR / f"{phase}.md"
    if progress_path.is_file():
        hits = check(
            progress_path.read_text(encoding="utf-8"),
            _show(str(repo), str(args.base), BRIEFS),
            phase,
        )
    else:
        hits = [Hit(rule="progress-missing", path=f"{PROGRESS_DIR}/{phase}.md", detail="")]
    _ = sys.stdout.write(json.dumps({"violations": hits}) + "\n")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
