"""Draw the Git page on the real core and a generated repository: light, dark, narrow.

Usage, on a display or under xvfb-run: python tools/git_snapshot.py --out tests/gui/snapshots/GIT
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final, NamedTuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import ProjectAddParams, ProjectView  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.client.daemon import core_python  # noqa: E402
from aconsole.ui.snapshot import capture_window  # noqa: E402
from aconsole.ui.viewmodels.git_vm import GitViewModel  # noqa: E402
from aconsole.ui.views.review.git_pane import GitPane, RowItem  # noqa: E402

_GIT: Final = "/usr/bin/git"
_BASE_COMMITS: Final = 24
_TIMEOUT_MS: Final = 60_000
_ATTEMPTS: Final = 500
_SETTLE_S: Final = 0.5
_SOCKET_WAIT: Final = 0.05
_POLL_S: Final = 0.02
_DIRTY: Final = "ac/docs-update"
_STYLE: Final = Path(__file__).parents[1] / "src" / "aconsole" / "ui" / "style" / "style.css"
# Without a window manager GTK draws the window's shadow inside the size it asks for.
_SHADOW: Final = 10


class _Shot(NamedTuple):
    name: str
    scheme: Adw.ColorScheme
    width: int
    height: int


_SHOTS: Final = (
    _Shot("git-light.png", Adw.ColorScheme.FORCE_LIGHT, 1280, 800),
    _Shot("git-dark.png", Adw.ColorScheme.FORCE_DARK, 1280, 800),
    _Shot("git-narrow.png", Adw.ColorScheme.FORCE_LIGHT, 700, 800),
)


def _git(repo: Path, *args: str) -> None:
    command = [_GIT, "-C", str(repo), "-c", "user.name=Dev", "-c", "user.email=dev@example.org", *args]
    _ = subprocess.run(command, check=True, capture_output=True)


def _commit(repo: Path, path: str, text: str, message: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", path)
    _git(repo, "commit", "-qm", message)


def _build(base: Path) -> Path:
    """Three agents' worktrees: one ready and behind, one in conflict, one with work not committed."""
    repo = base / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    for number in range(_BASE_COMMITS):
        _commit(repo, f"src/module_{number % 6}.py", f"VALUE = {number}\n", f"Step {number} of the groundwork")
    ready = base / "auth-login"
    _git(repo, "worktree", "add", "-q", "-b", "ac/auth-login", str(ready))
    _commit(ready, "src/auth.py", "def login() -> None: ...\n", "Add the login entry point")
    _commit(ready, "tests/test_auth.py", "def test_login() -> None: ...\n", "Cover the login entry point")
    conflict = base / "rate-limit"
    _git(repo, "worktree", "add", "-q", "-b", "ac/rate-limit", str(conflict))
    _commit(conflict, "src/module_0.py", "VALUE = 'limited'\n", "Limit the request rate")
    dirty = base / "docs-update"
    _git(repo, "worktree", "add", "-q", "-b", _DIRTY, str(dirty))
    _commit(dirty, "README.md", "# Project\n", "Start the readme")
    _commit(repo, "src/module_0.py", "VALUE = 'moved'\n", "Move the shared value")
    _ = (dirty / "README.md").write_text("# Project\n\nHow to run it.\n", encoding="utf-8")
    _git(dirty, "add", "--", "README.md")
    _ = (dirty / "src" / "module_1.py").write_text("VALUE = 'edited'\n", encoding="utf-8")
    _ = (dirty / "NOTES.md").write_text("A note not yet added.\n", encoding="utf-8")
    return repo


async def _settled(pane: GitPane) -> None:
    """Choose the worktree with uncommitted files, and wait for them to show."""
    for _ in range(_ATTEMPTS):
        for at in range(pane.store.get_n_items()):
            item = pane.store.get_item(at)
            if isinstance(item, RowItem) and item.row.worktree is not None and item.row.worktree.branch == _DIRTY:
                pane.selection.set_selected(at)
                if pane.file_list.get_first_child() is not None:
                    return
        await asyncio.sleep(_POLL_S)
    message = "the Git page did not settle"
    raise TimeoutError(message)


def _load_style() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_path(str(_STYLE))
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _wait_for(path: Path) -> bool:
    return path.exists()


def _draw(socket: Path, repo: Path, out: Path, failures: list[BaseException]) -> None:
    class _App(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id="io.github.aconsole.GitSnapshot")
            self._held: bool = False

        def do_activate(self) -> None:
            if self._held:
                return
            self._held = True
            self.hold()
            _load_style()
            _ = GLib.timeout_add(_TIMEOUT_MS, self._expired)
            _ = spawn_task(self._run())

        def _expired(self) -> bool:
            failures.append(TimeoutError("the snapshot did not finish"))
            self.quit()
            return GLib.SOURCE_REMOVE

        async def _run(self) -> None:
            client = CoreClient(str(socket))
            try:
                for _ in range(_ATTEMPTS):
                    if _wait_for(socket):
                        break
                    await asyncio.sleep(_SOCKET_WAIT)
                _ = await client.connect()
                _ = await client.pull_snapshot()
                project = await client.request("project/add", ProjectAddParams(root=str(repo)), ProjectView)
                for shot in _SHOTS:
                    Adw.StyleManager.get_default().set_color_scheme(shot.scheme)
                    pane = GitPane(GitViewModel(client, project.id), lambda _text: None)
                    window = Adw.ApplicationWindow(
                        application=self,
                        content=pane,
                        default_width=shot.width + _SHADOW,
                        default_height=shot.height + _SHADOW,
                    )
                    window.present()
                    await _settled(pane)
                    await asyncio.sleep(_SETTLE_S)
                    capture_window(window, out / shot.name)
                    window.destroy()
            except BaseException as error:  # noqa: BLE001 -- reported after the loop stops
                failures.append(error)
            finally:
                await client.close()
                self.release()
                self.quit()

    if run_app(_App, ["git-snapshot"]) != 0:
        failures.append(RuntimeError("the application failed"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--out", type=Path, default=Path("tests/gui/snapshots/GIT"))
    out: Path = parser.parse_args(argv).out
    out.mkdir(parents=True, exist_ok=True)
    failures: list[BaseException] = []
    with tempfile.TemporaryDirectory(prefix="acgit") as held:
        base = Path(held)
        repo = _build(base)
        socket = base / "s" / "core.sock"
        socket.parent.mkdir(mode=0o700)
        environment = dict(os.environ, XDG_DATA_HOME=str(base / "data"), XDG_STATE_HOME=str(base / "state"))
        command = [str(core_python()), "-m", "aconsole.core", "--serve", "--socket", str(socket)]
        daemon = subprocess.Popen(command, env=environment)
        try:
            _draw(socket, repo, out, failures)
        finally:
            daemon.terminate()
            _ = daemon.wait(timeout=10)
    if failures:
        raise failures[0]
    _ = sys.stdout.write(f"wrote {len(_SHOTS)} images to {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
