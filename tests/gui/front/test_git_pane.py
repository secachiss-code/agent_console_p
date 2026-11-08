"""The Git page against the real core and a real repository."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import gi

from tests.socket_paths import short_socket_path

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import ProjectAddParams, ProjectView  # noqa: E402
from aconsole.contracts.git import MergeProbeView, WorktreeGitState  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.snapshot import capture_window  # noqa: E402
from aconsole.ui.viewmodels.git_vm import GitViewModel, merge_badges  # noqa: E402
from aconsole.ui.views.review.git_pane import GitPane, RowItem  # noqa: E402
from tests.gui.core_daemon import core_command  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest

_TIMEOUT_MS = 30000
_GIT = "/usr/bin/git"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [_GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e", *args],
        check=True, capture_output=True, text=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, path: str, text: str, message: str) -> str:
    _ = (repo / path).write_text(text, encoding="utf-8")
    _ = _git(repo, "add", "--", path)
    _ = _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _project(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    _ = _commit(repo, "a.txt", "1\n", "first")
    tree = tmp_path / "wt"
    _ = _git(repo, "worktree", "add", "-q", "-b", "ac/feature-1", str(tree))
    _ = _commit(tree, "b.txt", "x\n", "agent work")
    point = _git(tree, "rev-parse", "HEAD")
    _ = _git(repo, "update-ref", "refs/aconsole/checkpoints/TAB/1", point)
    _ = _commit(repo, "a.txt", "base\n", "base moved")
    (tree / "c.txt").write_text("new\n", encoding="utf-8")
    return repo, tree


def _on_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    check: Callable[[GitPane, list[str], Adw.ApplicationWindow], Awaitable[None]],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    socket = short_socket_path(tmp_path / "s" / "core.sock")
    socket.parent.mkdir(exist_ok=True)
    socket.parent.chmod(0o700)
    command = core_command(socket)
    daemon = subprocess.Popen(command, env=dict(os.environ))
    failures: list[BaseException] = []

    class _App(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id="io.github.aconsole.GitPaneTest")
            self._held: bool = False

        def do_activate(self) -> None:
            if self._held:
                return
            self._held = True
            self.hold()
            _ = GLib.timeout_add(_TIMEOUT_MS, self._expired)
            _ = spawn_task(self._run())

        def _expired(self) -> bool:
            failures.append(TimeoutError("the pane did not settle"))
            self.quit()
            return GLib.SOURCE_REMOVE

        async def _run(self) -> None:
            client = CoreClient(str(socket))
            try:
                await _connected(client)
                _ = await client.pull_snapshot()
                project = await client.request("project/add", ProjectAddParams(root=str(repo)), ProjectView)
                toasts: list[str] = []
                pane = GitPane(GitViewModel(client, project.id), toasts.append)
                window = Adw.ApplicationWindow(application=self, content=pane, default_width=1280, default_height=800)
                window.present()
                await check(pane, toasts, window)
            except BaseException as error:  # noqa: BLE001 -- reported after the loop stops
                failures.append(error)
            finally:
                await client.close()
                self.release()
                self.quit()

    try:
        assert run_app(_App, ["git-pane-test"]) == 0
    finally:
        daemon.terminate()
        try:
            _ = daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
            _ = daemon.wait(timeout=5)
            raise
    if failures:
        raise failures[0]


async def _connected(client: CoreClient) -> None:
    """Until the core listens. Its socket file appears at bind, just before: no file and a refusal both mean wait."""
    for _ in range(600):
        try:
            _ = await client.connect()
        except (FileNotFoundError, ConnectionRefusedError):
            await asyncio.sleep(0.05)
        else:
            return
    _ = await client.connect()


async def _settled(check: Callable[[], bool]) -> None:
    for _ in range(400):
        if check():
            return
        await asyncio.sleep(0.02)
    message = "condition not reached"
    raise AssertionError(message)


def _rows(pane: GitPane) -> list[RowItem]:
    found: list[RowItem] = []
    for at in range(pane.store.get_n_items()):
        item = pane.store.get_item(at)
        assert isinstance(item, RowItem)
        found.append(item)
    return found


def _index(pane: GitPane, wanted: Callable[[RowItem], bool]) -> int:
    return next(at for at, item in enumerate(_rows(pane)) if wanted(item))


def _labels(widget: Gtk.Widget) -> list[str]:
    found: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_text())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def test_graph_rows_and_inspector_on_a_real_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, tree = _project(tmp_path)
    shot = os.environ.get("ACONSOLE_GIT_SHOT")

    async def check(pane: GitPane, _toasts: list[str], window: Adw.ApplicationWindow) -> None:
        await _settled(lambda: pane.store.get_n_items() >= 4)
        rows = _rows(pane)
        assert pane.state is not None
        assert pane.state.base_branch == "develop"
        assert rows[0].row.worktree is not None
        assert rows[0].row.worktree.path == str(tree.resolve())
        kinds = {item.row.node.kind for item in rows if item.row.node is not None}
        assert kinds == {"commit", "checkpoint"}

        # Uncommitted files: the new one shows, and only in the worktree mode.
        pane.selection.set_selected(0)
        await _settled(lambda: "c.txt" in _labels(pane.file_list))
        assert pane.source is not None
        assert pane.source.kind == "worktree"
        pane.mode_choice.set_selected(1)
        await _settled(lambda: "c.txt" not in _labels(pane.file_list))
        pane.mode_choice.set_selected(0)
        await _settled(lambda: "c.txt" in _labels(pane.file_list))
        if shot:
            await asyncio.sleep(0.3)
            capture_window(window, Path(shot))

        # A commit's own files.
        pane.selection.set_selected(
            _index(pane, lambda item: item.row.node is not None and item.row.node.subject == "base moved"),
        )
        await _settled(lambda: "a.txt" in _labels(pane.file_list))
        await _settled(lambda: pane.selected_path == "a.txt" and pane.diff_box.get_first_child() is not None)

        # A change outside the app keeps the selection.
        chosen = pane.selected_key
        _ = _commit(repo, "d.txt", "d\n", "from a terminal")
        await pane.refresh()
        await _settled(lambda: any(i.row.node is not None and i.row.node.subject == "from a terminal" for i in _rows(pane)))  # noqa: E501
        assert pane.selected_key == chosen
        selected = pane.selection.get_selected_item()
        assert isinstance(selected, RowItem)
        assert selected.row.key == chosen

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_long_history_loads_in_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    stream = "".join(
        f"commit refs/heads/develop\ncommitter t <t@e> {1700000000 + n} +0000\ndata 2\nc\n"
        f"M 100644 inline a.txt\ndata 2\n{n % 10}\n\n"
        for n in range(450)
    )
    _ = subprocess.run([_GIT, "-C", str(repo), "fast-import", "--quiet"], input=stream, text=True, check=True)
    _ = _git(repo, "checkout", "-qf", "develop")

    async def check(pane: GitPane, _toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        # The first page arrives whole. Scrolling may ask for the next one by itself.
        await _settled(lambda: pane.store.get_n_items() >= 200)
        before = pane.store.get_n_items()
        additions: list[tuple[int, int, int]] = []

        def changed(_store: object, position: int, removed: int, added: int) -> None:
            additions.append((position, removed, added))

        _ = pane.store.connect("items-changed", changed)
        for _ in range(4):
            await pane.load_more()
        await _settled(lambda: pane.store.get_n_items() == 450)
        keys = [item.row.key for item in _rows(pane)]
        assert len(set(keys)) == 450
        assert sum(added for _position, _removed, added in additions) == 450 - before
        assert all(removed == 0 for _position, removed, _added in additions)
        assert len(additions) <= 2, "each loaded page should emit a single model change"

    _on_pane(tmp_path, monkeypatch, repo, check)


def _row(**changes: object) -> WorktreeGitState:
    base: dict[str, object] = {
        "path": "/w", "is_main": False, "branch": "ac/x", "head_oid": "1" * 40, "detached": False, "tab_id": None,
        "tab_title": None, "agent": None, "tab_status": None, "busy": False, "plan_id": None, "plan_node": None,
        "start_oid": None, "ahead": 1, "behind": 0, "staged": 0, "unstaged": 0, "untracked": 0, "conflicted": 0,
        "dirty": False, "index_digest": "", "in_progress": None,
        "merge": MergeProbeView(status="clean", source_oid="1" * 40, target_oid="2" * 40),
    }
    base.update(changes)
    return WorktreeGitState(**base)  # pyright: ignore[reportArgumentType]


def test_behind_dirty_and_conflict_are_separate_badges() -> None:
    assert [item.key for item in merge_badges(_row())] == ["git.badge_ready"]
    behind_but_clean = merge_badges(_row(behind=3))
    assert [item.key for item in behind_but_clean] == ["git.badge_ready", "git.badge_behind"]
    dirty = merge_badges(_row(dirty=True))
    assert [item.key for item in dirty] == ["git.badge_ready", "git.badge_unchecked"]
    conflict = merge_badges(
        _row(behind=1, merge=MergeProbeView(status="conflict", source_oid="1", target_oid="2", conflicts=("a",))),
    )
    assert [item.key for item in conflict] == ["git.badge_conflict", "git.badge_behind"]
