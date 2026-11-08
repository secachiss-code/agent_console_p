"""An unopened terminal must refuse delivery instead of acknowledging lost text."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from aconsole.contracts.app import (
    CommandResult,
    EmptyResult,
    ExitedParams,
    ProjectAddParams,
    ProjectView,
    SendCommentParams,
    StartedParams,
    TabView,
)
from aconsole.contracts.events import InjectToTerminal
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path


def test_comment_to_unstarted_tab_is_refused(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", _open(project.id), TabView)
            result = await client.call("diff/sendComment", SendCommentParams(
                tab_id=tab.id, path="f", hunk_id="h", text="keep this comment",
            ), CommandResult)
            assert not result.ok
            assert result.toast == "review.agent_not_started"
            assert not daemon.review.send_text(str(tab.id), "message").ok
            await client.call("tab/started", StartedParams(tab_id=tab.id, pid=os.getpid()), EmptyResult)
            delivered = await client.call("diff/sendComment", SendCommentParams(
                tab_id=tab.id, path="f", hunk_id="h", text="deliver after start",
            ), CommandResult)
            assert delivered.ok
            events = daemon._ring.since(0)
            assert events is not None
            assert any(isinstance(item.event, InjectToTerminal) for item in events)
            await client.call("tab/exited", ExitedParams(tab_id=tab.id, wait_status=0), EmptyResult)
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(run())
