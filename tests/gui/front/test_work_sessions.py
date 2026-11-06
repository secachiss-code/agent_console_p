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

from aconsole.contracts.work_sessions import WorkSessionCreateParams, WorkSessionParams, WorkSessionView  # noqa: E402
from aconsole.ui.snapshot import capture_window  # noqa: E402
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
        dialog.emit("response", "confirm")
        dialog.force_close()
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
        pane.sessions.dialog.emit("response", "confirm")
        pane.sessions.dialog.force_close()
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
        pane.sessions.dialog.emit("response", "confirm")
        pane.sessions.dialog.force_close()
        await status("active")
        assert toasts == []

    _on_pane(tmp_path, monkeypatch, repo, check)


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
