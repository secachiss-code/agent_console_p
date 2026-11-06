"""Task context and lifecycle controls against the real daemon, without launching agent processes."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from aconsole.contracts.app import ProjectAddParams, ProjectView  # noqa: E402
from aconsole.contracts.work_sessions import WorkSessionCreateParams, WorkSessionParams, WorkSessionView  # noqa: E402
from aconsole.ui.snapshot import capture_window  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.review.messages import _ as translate  # noqa: E402
from aconsole.ui.views.tabs.new_tab import NewTabDialog  # noqa: E402
from tests.gui.front.test_git_pane import _commit, _git, _on_pane, _settled  # noqa: E402

if TYPE_CHECKING:
    import pytest

    from aconsole.ui.views.review.git_pane import GitPane


def test_session_picker_rename_archive_and_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    _ = _commit(repo, "a", "base\n", "first")

    async def check(pane: GitPane, toasts: list[str], window: Adw.ApplicationWindow) -> None:
        session = await pane.viewmodel.client.request(
            "workSession/create",
            WorkSessionCreateParams(
                project_id=pane.viewmodel.project_id,
                title="Git session",
            ),
            WorkSessionView,
        )
        await pane.refresh()
        assert pane.sessions.get_visible()
        pane.sessions.choice.set_selected(1)
        await _settled(
            lambda: pane.viewmodel.work_session_id == session.id and pane.sessions.buttons["rename"].get_sensitive()
        )
        await _settled(lambda: pane.state is not None and len(pane.state.worktrees) == 1)
        assert pane.state.worktrees[0].path == session.worktree

        pane.sessions.buttons["rename"].emit("clicked")
        await _settled(lambda: pane.sessions.dialog is not None)
        dialog = pane.sessions.dialog
        assert dialog is not None
        entry = dialog.get_extra_child()
        assert isinstance(entry, Gtk.Entry)
        entry.set_text("Git session renamed")
        await _confirm(dialog)
        await _settled(lambda: not pane.sessions._busy)
        params = WorkSessionParams(project_id=pane.viewmodel.project_id, work_session_id=session.id)

        async def status(expected: str) -> None:
            for _ in range(100):
                current = await pane.viewmodel.client.request("workSession/get", params, WorkSessionView)
                if current.status == expected and current.title == "Git session renamed":
                    return
                await asyncio.sleep(0.02)
            message = "session state did not settle"
            raise AssertionError(message)

        await status("active")
        pane.sessions.dialog = None
        pane.sessions.buttons["archive"].emit("clicked")
        await _settled(lambda: pane.sessions.dialog is not None)
        await _confirm(pane.sessions.dialog)
        await status("archived")
        assert await asyncio.to_thread(Path(session.worktree).is_dir)
        await pane.refresh()
        assert not pane.sessions.buttons["handoff"].get_sensitive()
        shot = os.environ.get("ACONSOLE_WORK_SESSION_SHOT")
        if shot:
            await _screenshots(window, Path(shot))
        pane.sessions.dialog = None
        pane.sessions.buttons["archive"].emit("clicked")
        await _settled(lambda: pane.sessions.dialog is not None)
        await _confirm(pane.sessions.dialog)
        await status("active")
        await _switch_project(pane, repo, session)
        assert toasts == []

    _on_pane(tmp_path, monkeypatch, repo, check)


async def _switch_project(pane: GitPane, repo: Path, session: WorkSessionView) -> None:
    other = repo.parent / "other"
    other.mkdir()
    _ = _git(other, "init", "-q", "-b", "other-base")
    _ = await pane.viewmodel.client.request("project/add", ProjectAddParams(root=str(other)), ProjectView)
    _ = await pane.viewmodel.client.pull_snapshot()
    dialog = NewTabDialog(TabsViewModel(pane.viewmodel.client), lambda _tab: None, lambda _error: None)
    project = next(row for row in dialog._projects if row.id == session.project_id)
    dialog.reload(project)
    await _settled(lambda: any(row.id == session.id for row in dialog._sessions))
    dialog.session_row.set_selected(1)
    assert not dialog.workspace_row.get_visible()
    # Reset selection before indexing the new project's asynchronously loaded session list.
    target = next(at for at, row in enumerate(dialog._projects) if row.id != project.id)
    dialog.project_row.set_selected(target)
    assert dialog.workspace_row.get_visible()
    assert dialog._sessions == ()
    assert str(other) in dialog.location.get_label()


async def _confirm(dialog: Adw.AlertDialog) -> None:
    await _settled(dialog.get_mapped)
    # A mapped dialog may still be entering; let its presentation finish before clicking.
    await asyncio.sleep(0.3)

    def click(widget: Gtk.Widget) -> bool:
        if isinstance(widget, Gtk.Button) and widget.get_label() == translate("git.session_confirm"):
            widget.emit("clicked")
            return True
        child = widget.get_first_child()
        while child is not None:
            if click(child):
                return True
            child = child.get_next_sibling()
        return False

    assert click(dialog)
    await _settled(lambda: not dialog.get_mapped())


async def _screenshots(window: Adw.ApplicationWindow, output: Path) -> None:
    manager = Adw.StyleManager.get_default()
    previous = manager.get_color_scheme()
    try:
        for name, scheme, width in (
            ("light", Adw.ColorScheme.FORCE_LIGHT, 1280),
            ("dark", Adw.ColorScheme.FORCE_DARK, 1280),
            ("narrow", Adw.ColorScheme.FORCE_LIGHT, 700),
        ):
            manager.set_color_scheme(scheme)
            window.set_default_size(width, 800)
            await asyncio.sleep(0.3)
            capture_window(window, output.with_stem(f"{output.stem}-{name}"))
    finally:
        manager.set_color_scheme(previous)
