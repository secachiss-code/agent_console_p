"""done_when: the progress file copies the brief's "Готово, когда" word for word, read from the task base."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools.gate import done_when

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "gate" / "done_when.py"
PHASE = "A9.1"
PROGRESS_REL = "docs/plan/core/progress/A9.1.md"
CORE_REL = "docs/plan/CORE.md"
DONE_TEXT = "`hostrun gate A9.1` → 0; `tests/core/test_x.py` зелёный."
BRIEFS = (
    "# План\n\n"
    "**A9.0 · Соседняя** — **Файлы:** `src/x.py`. **Готово, когда:** `hostrun gate A9.0` → 0. **Запрещено:** всё.\n\n"
    "**A9.1 · Пример задачи** — **Читать:** `docs/x.md`. **Файлы:** `tests/core/test_x.py`. **Шаги:** написать тест. "
    f"**Готово, когда:** {DONE_TEXT} **Запрещено:** baseline.\n\n"
    "**A9.2 · Зависимая** — **Готово, когда:** `hostrun gate A9.2` → 0. **Зависит от:** A9.1.\n"
)
VERBATIM_ITEMS = "- [ ] `hostrun gate A9.1` → 0\n- [ ] `tests/core/test_x.py` зелёный."


def _progress(items: str, status: str = "doing") -> str:
    return f"# A9.1 — example\n\nStatus: {status}\n\n## Done when\n{items}\n\n## Steps\n- [ ] write the test\n"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=gate@example.com", "-c", "user.name=gate", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _commit_repo(tmp: Path, core: str, progress: str) -> str:
    _ = _git(tmp, "init")
    _write(tmp / CORE_REL, core)
    _write(tmp / PROGRESS_REL, progress)
    _ = _git(tmp, "add", ".")
    _ = _git(tmp, "commit", "-m", "base")
    return _git(tmp, "rev-parse", "HEAD")


def _run_cli(repo: Path, base: str, phase: str = PHASE) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--base", base, "--phase", phase],
        capture_output=True,
        text=True,
        check=False,
    )


def _rules(hits: list[done_when.Hit]) -> list[str]:
    return [hit["rule"] for hit in hits]


# brief_done_when


def test_brief_done_when_stops_at_the_forbidden_marker() -> None:
    assert done_when.brief_done_when(BRIEFS, PHASE) == DONE_TEXT


def test_brief_done_when_stops_at_the_depends_on_marker() -> None:
    assert done_when.brief_done_when(BRIEFS, "A9.2") == "`hostrun gate A9.2` → 0."


def test_brief_done_when_matches_the_paragraph_start_exactly() -> None:
    # "**A9.1 ·" is not a prefix of "**A9.10 ·", and a mention inside another paragraph does not count.
    briefs = (
        "Смотри **A9.1 · чужое** — **Готово, когда:** `wrong`.\n\n"
        "**A9.10 · Другая** — **Готово, когда:** `also wrong`.\n\n" + BRIEFS
    )
    assert done_when.brief_done_when(briefs, PHASE) == DONE_TEXT


def test_brief_done_when_accepts_a_list_item_paragraph() -> None:
    briefs = "- **A9.3 · Список** — **Готово, когда:** `list item` → 0. **Запрещено:** x.\n"
    assert done_when.brief_done_when(briefs, "A9.3") == "`list item` → 0."


def test_brief_done_when_normalises_whitespace() -> None:
    briefs = "**A9.5 · Пробелы** — **Готово, когда:** `hostrun\n   gate A9.5`  → 0\n"
    assert done_when.brief_done_when(briefs, "A9.5") == "`hostrun gate A9.5` → 0"


def test_brief_done_when_is_none_for_unknown_phase_or_missing_criterion() -> None:
    assert done_when.brief_done_when(BRIEFS, "A8.9") is None
    assert done_when.brief_done_when("**A9.4 · Без критерия** — **Файлы:** `x`.\n", "A9.4") is None


# progress_items


def test_progress_items_reads_only_the_done_when_section() -> None:
    progress = (
        "## Steps\n- [x] step one\n\n"
        "## Done when\n- [x] `a` one\n- [ ] `b` two\n- [X]   three   spaced\n\n"
        "## Evidence\n- [ ] not a done-when item\n"
    )
    assert done_when.progress_items(progress) == [
        (True, "`a` one"),
        (False, "`b` two"),
        (True, "three spaced"),
    ]


def test_progress_items_without_the_section_is_empty() -> None:
    assert done_when.progress_items("## Steps\n- [ ] only steps\n") == []


# check


def test_check_verbatim_items_pass() -> None:
    assert done_when.check(_progress(VERBATIM_ITEMS), BRIEFS, PHASE) == []


def test_check_verbatim_pass_ignores_whitespace_differences() -> None:
    items = "- [x] `hostrun   gate A9.1`  →  0\n- [ ] `tests/core/test_x.py` зелёный."
    assert done_when.check(_progress(items), BRIEFS, PHASE) == []


def test_check_whole_text_as_one_item_passes() -> None:
    assert done_when.check(_progress(f"- [ ] {DONE_TEXT}"), BRIEFS, PHASE) == []


def test_check_reworded_item_fails() -> None:
    items = "- [ ] `hostrun gate A9.1` returns zero\n- [ ] `tests/core/test_x.py` зелёный."
    hits = done_when.check(_progress(items), BRIEFS, PHASE)
    assert _rules(hits) == ["done-when-rewritten"]
    assert "returns zero" in hits[0]["detail"]
    assert hits[0]["path"] == PROGRESS_REL


def test_check_dropped_command_fails() -> None:
    hits = done_when.check(_progress("- [ ] `hostrun gate A9.1` → 0"), BRIEFS, PHASE)
    assert _rules(hits) == ["done-when-dropped"]
    assert hits[0]["detail"] == "`tests/core/test_x.py`"


def test_check_done_with_an_open_item_fails() -> None:
    items = "- [x] `hostrun gate A9.1` → 0\n- [ ] `tests/core/test_x.py` зелёный."
    assert _rules(done_when.check(_progress(items, status="done"), BRIEFS, PHASE)) == ["done-with-open-items"]


def test_check_done_with_every_item_ticked_passes() -> None:
    items = "- [x] `hostrun gate A9.1` → 0\n- [X] `tests/core/test_x.py` зелёный."
    assert done_when.check(_progress(items, status="done"), BRIEFS, PHASE) == []


def test_check_open_items_while_doing_are_fine() -> None:
    items = "- [x] `hostrun gate A9.1` → 0\n- [ ] `tests/core/test_x.py` зелёный."
    assert done_when.check(_progress(items, status="doing"), BRIEFS, PHASE) == []


def test_check_missing_brief_fails() -> None:
    assert _rules(done_when.check(_progress(VERBATIM_ITEMS), None, PHASE)) == ["brief-not-found"]
    assert _rules(done_when.check(_progress(VERBATIM_ITEMS), BRIEFS, "A8.9")) == ["brief-not-found"]


def test_check_without_items_fails() -> None:
    assert _rules(done_when.check(_progress(""), BRIEFS, PHASE)) == ["done-when-empty"]


# CLI on a temporary repository


def test_cli_passes_a_verbatim_copy(tmp_path: Path) -> None:
    base = _commit_repo(tmp_path, BRIEFS, _progress(VERBATIM_ITEMS))
    result = _run_cli(tmp_path, base)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"violations": []}


def test_cli_fails_a_reworded_copy_with_exit_1(tmp_path: Path) -> None:
    base = _commit_repo(tmp_path, BRIEFS, _progress("- [ ] `hostrun gate A9.1` returns zero"))
    result = _run_cli(tmp_path, base)
    assert result.returncode == 1
    rules = [item["rule"] for item in json.loads(result.stdout)["violations"]]
    assert "done-when-rewritten" in rules
    assert "done-when-dropped" in rules


def test_cli_reads_the_brief_at_the_base_not_the_edited_file(tmp_path: Path) -> None:
    base = _commit_repo(tmp_path, BRIEFS, _progress(VERBATIM_ITEMS))
    edited = BRIEFS.replace(DONE_TEXT, "`hostrun gate A9.1` → 0.")
    # An uncommitted edit of CORE.md does not change the result.
    _write(tmp_path / CORE_REL, edited)
    uncommitted = _run_cli(tmp_path, base)
    assert uncommitted.returncode == 0
    assert json.loads(uncommitted.stdout) == {"violations": []}
    # A committed edit does not either: the base sha still names the original brief.
    _ = _git(tmp_path, "add", ".")
    _ = _git(tmp_path, "commit", "-m", "edit brief")
    committed = _run_cli(tmp_path, base)
    assert committed.returncode == 0
    assert json.loads(committed.stdout) == {"violations": []}
    # The edited text is judged only against the edited base, and passes there.
    _write(tmp_path / PROGRESS_REL, _progress("- [ ] `hostrun gate A9.1` → 0."))
    edited_base = _git(tmp_path, "rev-parse", "HEAD")
    assert _run_cli(tmp_path, base).returncode == 1
    assert _run_cli(tmp_path, edited_base).returncode == 0


def test_cli_missing_progress_file_fails(tmp_path: Path) -> None:
    base = _commit_repo(tmp_path, BRIEFS, _progress(VERBATIM_ITEMS))
    _ = _git(tmp_path, "rm", "-q", PROGRESS_REL)
    _ = _git(tmp_path, "commit", "-m", "drop progress")
    result = _run_cli(tmp_path, base)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "violations": [{"rule": "progress-missing", "path": PROGRESS_REL, "detail": ""}],
    }


def test_cli_unknown_base_has_no_brief(tmp_path: Path) -> None:
    _ = _commit_repo(tmp_path, BRIEFS, _progress(VERBATIM_ITEMS))
    result = _run_cli(tmp_path, "0" * 40)
    assert result.returncode == 1
    rules = [item["rule"] for item in json.loads(result.stdout)["violations"]]
    assert rules == ["brief-not-found"]


@pytest.mark.parametrize("status", ["done.", "Done", "**done**", "DONE", "done (audit pending)"])
def test_status_done_variants_with_an_open_item_are_flagged(status: str) -> None:
    brief = "**A9.1 · X** — **Готово, когда:** `hostrun gate A9.1` → 0; `t/x.py` passes. **Запрещено:** y.\n"
    progress = f"Status: {status}\n\n## Done when\n- [x] `hostrun gate A9.1` → 0\n- [ ] `t/x.py` passes.\n"
    rules = [hit["rule"] for hit in done_when.check(progress, brief, "A9.1")]
    assert "done-with-open-items" in rules


def test_empty_and_fragment_items_are_rewrites() -> None:
    brief = "**A9.1 · X** — **Готово, когда:** `hostrun gate A9.1` → 0. **Запрещено:** y.\n"
    progress = "Status: doing\n\n## Done when\n- [ ] `hostrun gate A9.1` → 0\n- [ ] 0\n- [ ]\n"
    details = [
        hit["detail"] for hit in done_when.check(progress, brief, "A9.1") if hit["rule"] == "done-when-rewritten"
    ]
    assert details == ["0", "(empty item)"]
