"""The window against the real daemon, not the fake: the first thing a user runs."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from aconsole.contracts.app import ProjectAddParams, ProjectView
from aconsole.ui.bootstrap import run_app
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.daemon import core_python
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
