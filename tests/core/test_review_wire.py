"""The assembled daemon: a prompt makes a checkpoint, diff reads the tab, and restore puts files back."""

from __future__ import annotations

import asyncio
import json
import subprocess
import urllib.request
from typing import TYPE_CHECKING

from aconsole.contracts.app import (
    CheckpointIdParams,
    CheckpointListParams,
    CheckpointListResult,
    CommandResult,
    DiffFileParams,
    DiffFileResult,
    DiffSummaryParams,
    DiffSummaryResult,
    ProjectAddParams,
    ProjectView,
    RestoreParams,
    RestorePreviewResult,
    RestoreResult,
    SendCommentParams,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.events import CheckpointCreated, InjectToTerminal, UiEvent
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

_GIT = "/usr/bin/git"


def _commit(repo: Path) -> None:
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    for args in (
        ("add", "a.txt"),
        ("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-qm", "base"),
    ):
        _ = subprocess.run([_GIT, "-C", str(repo), *args], check=True)


def _refs(repo: Path) -> list[str]:
    listed = subprocess.run(
        [_GIT, "-C", str(repo), "for-each-ref", "--format=%(refname)", "refs/aconsole"],
        check=True, capture_output=True, text=True,
    )
    return listed.stdout.split()


def _hook(port: int, tab: str, token: str, body: dict[str, object]) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/tabs/{tab}/claude/hook",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as reply:  # noqa: S310 -- loopback only
        _ = reply.read()


async def _eventually(check: Callable[[], Awaitable[bool]], tries: int = 300) -> None:
    for _ in range(tries):
        if await check():
            return
        await asyncio.sleep(0.02)
    message = "condition not reached"
    raise AssertionError(message)


def test_prompt_checkpoint_diff_restore_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = _git_repo(tmp_path / "repo")
        _commit(repo)
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call(
            "tab/open",
            _open(project.id),
            TabView,
        )
        spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
        token = spec.env["ACONSOLE_TAB_TOKEN"]
        port = daemon.ingest_port
        assert port is not None

        (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        await asyncio.to_thread(
            _hook, port, str(tab.id), token,
            {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "first line\nsecond"},
        )

        async def has_one() -> bool:
            listed = await client.call("checkpoint/list", CheckpointListParams(tab_id=tab.id), CheckpointListResult)
            return len(listed.checkpoints) == 1

        await _eventually(has_one)
        listed = await client.call("checkpoint/list", CheckpointListParams(tab_id=tab.id), CheckpointListResult)
        point = listed.checkpoints[0]
        assert point.label == "first line"
        assert (point.files_changed, point.insertions) == (1, 1)

        summary = await client.call("diff/summary", DiffSummaryParams(tab_id=tab.id), DiffSummaryResult)
        assert [item.path for item in summary.files] == ["a.txt"]
        detail = await client.call("diff/file", DiffFileParams(tab_id=tab.id, path="a.txt"), DiffFileResult)
        assert any(line.kind == "add" and "two" in line.text for hunk in detail.hunks for line in hunk.lines)

        (repo / "a.txt").write_text("changed later\n", encoding="utf-8")
        preview = await client.call(
            "checkpoint/restorePreview", CheckpointIdParams(checkpoint_id=point.id), RestorePreviewResult,
        )
        assert "a.txt" in preview.files

        # The prompt left the tab working: the restore waits, and the files stay.
        assert await client.error(
            "checkpoint/restore",
            RestoreParams(checkpoint_id=point.id, confirm_token=preview.confirm_token),
        ) == "CHECKPOINT_RESTORE_CONFLICT"
        assert (repo / "a.txt").read_text(encoding="utf-8") == "changed later\n"
        _ = await daemon._in_worker(lambda: daemon.tabs.apply_hook(str(tab.id), "Stop"))
        assert await client.error(
            "checkpoint/restore", RestoreParams(checkpoint_id=point.id, confirm_token="wrong"),  # noqa: S106
        ) == "CONFIRM_TOKEN_INVALID"
        done = await client.call(
            "checkpoint/restore",
            RestoreParams(checkpoint_id=point.id, confirm_token=preview.confirm_token),
            RestoreResult,
        )
        assert done.restored
        assert (repo / "a.txt").read_text(encoding="utf-8") == "one\ntwo\n"
        refs = await asyncio.to_thread(_refs, repo)
        assert len(refs) == 2  # the checkpoint and the safety copy of "changed later"

        await _stop(daemon, task, client)

        again = _daemon(tmp_path)
        restarted = asyncio.create_task(again.serve())
        second = await _connect(again.socket)
        kept = await second.call("checkpoint/list", CheckpointListParams(tab_id=tab.id), CheckpointListResult)
        assert [item.turn for item in kept.checkpoints] == [1, 2]
        reread = await second.call(
            "checkpoint/restorePreview", CheckpointIdParams(checkpoint_id=point.id), RestorePreviewResult,
        )
        assert "a.txt" in reread.files
        await _stop(again, restarted, second)

    asyncio.run(scenario())


def test_review_errors_answer_the_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        missing = "01JABCDEFGHJKMNPQRSTVWXYZ0"
        assert await client.error("diff/summary", DiffSummaryParams(tab_id=missing)) == "TAB_NOT_FOUND"
        assert await client.error("checkpoint/list", CheckpointListParams(tab_id=missing)) == "TAB_NOT_FOUND"
        assert await client.error(
            "checkpoint/restorePreview", CheckpointIdParams(checkpoint_id="nope-1"),
        ) == "CHECKPOINT_NOT_FOUND"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_comment_waits_for_a_working_tab_and_reaches_its_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = _git_repo(tmp_path / "repo")
        _commit(repo)
        daemon = _daemon(tmp_path, serve_ingest=False)
        published: list[UiEvent] = []
        original = daemon._publish

        def spy(event: UiEvent, live: int, token: str | None) -> None:
            published.append(event)
            original(event, live, token)

        daemon._publish = spy
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call(
            "tab/open",
            _open(project.id),
            TabView,
        )
        _ = await daemon._in_worker(lambda: daemon.tabs.apply_hook(str(tab.id), "UserPromptSubmit"))
        sent = await client.call(
            "diff/sendComment",
            SendCommentParams(tab_id=tab.id, path="a.txt", hunk_id="h1", text="поправь"),
            CommandResult,
        )
        assert sent.toast is not None
        assert not any(isinstance(event, InjectToTerminal) for event in published)
        _ = await daemon._in_worker(lambda: daemon.tabs.apply_hook(str(tab.id), "Stop"))
        await asyncio.sleep(0.05)
        injected = [event for event in published if isinstance(event, InjectToTerminal)]
        assert len(injected) == 1
        assert "поправь" in injected[0].text
        assert not any(isinstance(event, CheckpointCreated) for event in published)
        await _stop(daemon, task, client)

    asyncio.run(scenario())

