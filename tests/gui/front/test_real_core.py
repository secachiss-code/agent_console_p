"""The window against the real daemon, not the fake: the first thing a user runs."""

from __future__ import annotations

import os
import subprocess
import time
from typing import TYPE_CHECKING

from aconsole.contracts.app import ProjectAddParams, ProjectView
from aconsole.ui.bootstrap import run_app
from aconsole.ui.client.core_age import core_is_older_than_code
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.daemon import core_python
from aconsole.ui.views import app as app_module
from aconsole.ui.views.app import AConsoleApp, SnapshotRequest

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _RealApp(AConsoleApp):
    project: ProjectView | None = None
    repo: str = ""

    async def _open(self) -> None:
        await super()._open()
        if not self.failed:
            self.project = await self.client.request("project/add", ProjectAddParams(root=self.repo), ProjectView)


def test_window_opens_on_the_real_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    socket = tmp_path / "aconsole" / "core.sock"
    out = tmp_path / "real.png"
    client = CoreClient(str(socket))
    command = [str(core_python()), "-m", "aconsole.core", "--serve", "--socket", str(socket)]
    request = SnapshotRequest(view="window", theme="light", width=1280, height=800, out=out)
    app = _RealApp(client, request, core_command=command)
    app.repo = str(repo)
    try:
        assert run_app(lambda: app, ["aconsole-real-core"]) == 0
    finally:
        pid = client.daemon_pid
        if pid is not None:
            _ = subprocess.run(["/usr/bin/kill", str(pid)], check=False)
    assert not app.failed
    assert client.keep_daemon
    assert pid is not None
    assert out.stat().st_size > 0
    snapshot = client.store.snapshot
    assert snapshot is not None
    assert snapshot.tabs == ()


def test_window_replaces_a_core_older_than_the_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
    socket = tmp_path / "aconsole" / "core.sock"
    socket.parent.mkdir()
    command = [str(core_python()), "-m", "aconsole.core", "--serve", "--socket", str(socket)]
    old = subprocess.Popen(command)
    deadline = time.monotonic() + 20
    while not socket.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    monkeypatch.setattr(app_module, "core_is_older_than_code", lambda pid: pid == old.pid)
    client = CoreClient(str(socket))
    request = SnapshotRequest(view="window", theme="light", width=1280, height=800, out=tmp_path / "real.png")
    app = _RealApp(client, request, core_command=command)
    app.repo = str(repo)
    try:
        assert run_app(lambda: app, ["aconsole-real-core"]) == 0
        assert old.wait(timeout=5) == 0
    finally:
        old.kill()
        pid = client.daemon_pid
        if pid is not None:
            _ = subprocess.run(["/usr/bin/kill", str(pid)], check=False)
    assert not app.failed
    assert pid is not None
    assert pid != old.pid


def test_core_age_compares_the_kernel_start_with_the_source(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    (proc / "7").mkdir(parents=True)
    ticks = 100 * os.sysconf("SC_CLK_TCK")
    fields = " ".join(["S", *["0"] * 18, str(ticks)])
    _ = (proc / "7" / "stat").write_text(f"7 (py thon) {fields}\n", encoding="ascii")
    _ = (proc / "stat").write_text("cpu 0\nbtime 1000\n", encoding="ascii")
    source = tmp_path / "src"
    (source / "core").mkdir(parents=True)
    code = source / "core" / "a.py"
    code.touch()
    os.utime(code, (1050, 1050))
    assert not core_is_older_than_code(7, source=source, proc=proc)
    os.utime(code, (1200, 1200))
    assert core_is_older_than_code(7, source=source, proc=proc)
    assert not core_is_older_than_code(8, source=source, proc=proc)
