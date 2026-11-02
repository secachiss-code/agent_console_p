"""A Codex turn seen through its session file."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aconsole.contracts.app import OpenTabParams, StartedParams
from aconsole.contracts.common import AgentKind, ProjectId, TabStatus, WorkspaceMode
from aconsole.core.application.services.tabs import ProjectSeed, TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.agents.codex.turns import TurnWatch
from tests.core.conftest import FakeTabPorts

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 11, 3, 1, 0)  # noqa: DTZ001 -- Codex names its folders by local date


def _line(kind: str, payload_type: str) -> str:
    return json.dumps({"type": kind, "payload": {"type": payload_type, "cwd": "/work"}}) + "\n"


def test_turn_start_and_end_are_reported_once(tmp_path: Path) -> None:
    folder = tmp_path / "2026" / "11" / "03"
    folder.mkdir(parents=True)
    session = folder / "rollout-a.jsonl"
    watch = TurnWatch(tmp_path)
    assert watch.changes(_NOW) == ()
    _ = session.write_text(_line("session_meta", "x"), encoding="utf-8")
    assert watch.changes(_NOW) == ()
    with session.open("a", encoding="utf-8") as file:
        _ = file.write(_line("event_msg", "task_started"))
    assert watch.changes(_NOW) == (("/work", True),)
    assert watch.changes(_NOW) == ()
    with session.open("a", encoding="utf-8") as file:
        _ = file.write(_line("event_msg", "token_count"))
    assert watch.changes(_NOW) == (("/work", True),)
    with session.open("a", encoding="utf-8") as file:
        _ = file.write(_line("event_msg", "task_complete"))
    assert watch.changes(_NOW) == (("/work", False),)


def test_codex_waits_for_the_user_until_new_lines_appear() -> None:
    start = datetime(2026, 11, 3, 1, 0, tzinfo=UTC)
    service = TabService(FrozenClock(start), FakeTabPorts())
    service.add_project(ProjectSeed(project_id="proj", root="/repo", container="agentbox", sessions=frozenset()))
    opened = service.open_tab(
        OpenTabParams(project_id=ProjectId("proj"), agent=AgentKind.CODEX, workspace=WorkspaceMode.MAIN_CHECKOUT),
    )
    _ = service.started(StartedParams(tab_id=opened.id, pid=5))
    cwd = opened.worktree_path
    status = lambda: service.snapshot().tabs[0].status  # noqa: E731
    service.codex_turn(cwd, working=True)
    assert status() is TabStatus.WORKING
    service.agent_notice(str(opened.id), "Approval requested: rm -rf build")
    assert status() is TabStatus.WAITING_APPROVAL
    service.codex_turn(cwd, working=True)
    assert status() is TabStatus.WAITING_APPROVAL, "lines written right after asking do not end the wait"
    service._clock = FrozenClock(start + timedelta(seconds=3))  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    service.codex_turn(cwd, working=True)
    assert status() is TabStatus.WORKING
    service.agent_notice(str(opened.id), "Agent turn complete")
    assert status() is TabStatus.WORKING
    service.codex_turn(cwd, working=False)
    assert status() is TabStatus.IDLE
