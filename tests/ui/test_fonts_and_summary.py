"""Font lists, the Git section's counts, and what the interface remembers between runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aconsole.contracts.common import ProjectId
from aconsole.contracts.git import GitStateResult, MergeProbeView, WorktreeGitState
from aconsole.ui.client.ui_state import UiState
from aconsole.ui.viewmodels.git_vm import git_summary
from aconsole.ui.viewmodels.logic.fonts import add_family, css_font_family, font_families, pango_family

if TYPE_CHECKING:
    from pathlib import Path


def test_a_font_list_is_cleaned_and_ends_in_monospace() -> None:
    families = font_families(' "JetBrains Mono" ,  Noto Sans Mono CJK SC,, jetbrains mono, monospace ')
    assert families == ("JetBrains Mono", "Noto Sans Mono CJK SC", "monospace")
    assert pango_family(families) == "JetBrains Mono,Noto Sans Mono CJK SC,monospace"
    assert css_font_family(families) == '"JetBrains Mono", "Noto Sans Mono CJK SC", monospace'
    assert font_families("") == font_families(None) == ()


def test_a_css_font_name_cannot_close_the_rule() -> None:
    assert css_font_family(('Evil"; } * { color: red',)) == '"Evil  *  color: red", monospace'


def test_the_chooser_adds_a_family_once() -> None:
    assert add_family("Fira Code", "Symbols Nerd Font") == "Fira Code, Symbols Nerd Font"
    assert add_family("Fira Code", "fira code") == "Fira Code"
    assert add_family(None, "Iosevka") == "Iosevka"


def _row(path: str, **values: object) -> WorktreeGitState:
    base: dict[str, object] = {
        "path": path, "is_main": False, "branch": f"ac/{path}", "head_oid": "a" * 40, "detached": False,
        "tab_id": None, "tab_title": None, "agent": None, "tab_status": None, "busy": False, "plan_id": None,
        "plan_node": None, "start_oid": None, "ahead": 0, "behind": 0, "staged": 0, "unstaged": 0,
        "untracked": 0, "conflicted": 0, "dirty": False, "index_digest": "", "in_progress": None,
        "merge": MergeProbeView(status="not_applicable", source_oid=None, target_oid=None),
    }
    base.update(values)
    return WorktreeGitState(**base)  # pyright: ignore[reportArgumentType]


def test_the_git_section_counts_worktrees_changes_merges_and_conflicts() -> None:
    clean = MergeProbeView(status="clean", source_oid="b", target_oid="c")
    conflict = MergeProbeView(status="conflict", source_oid="b", target_oid="c", conflicts=("a.py",))
    state = GitStateResult(
        project_id=ProjectId("p"), revision="r", root="/r", empty=False, base_branch="main", base_oid="c",
        worktrees=(
            _row("main", is_main=True, branch="main", dirty=True),
            _row("one", ahead=2, merge=clean),
            _row("two", ahead=1, dirty=True, merge=conflict),
            _row("three"),
        ),
        branches=(), operations=(),
    )
    summary = git_summary(state)
    assert (summary.base_branch, summary.worktrees, summary.changed, summary.ready, summary.conflicted) == (
        "main", 3, 2, 1, 1,
    )


def test_folded_sections_survive_a_restart_and_a_bad_file(tmp_path: Path) -> None:
    path = tmp_path / "aconsole" / "ui.json"
    first = UiState(path)
    assert not first.folded("plan")
    first.set_folded("plan", folded=True)
    assert UiState(path).folded("plan")
    _ = path.write_text("not json", encoding="utf-8")
    assert not UiState(path).folded("plan")
