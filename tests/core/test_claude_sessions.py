"""Claude sessions keep metadata only, and the skill cache drops when mtime changes."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from aconsole.contracts.app import OpenTabParams, TabIdParams
from aconsole.contracts.common import AgentKind, PaletteKind, ProjectId, TabId, WorkspaceMode
from aconsole.core.application.services.tabs import ProjectSeed, TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.agents.claude.sessions import list_sessions, project_key, resume_argv
from aconsole.core.infrastructure.agents.claude.skills import SkillDiscovery

_WHEN = datetime(2026, 10, 27, tzinfo=UTC)
_HIDDEN = "BODY-MUST-NOT-LEAK"
_SESSION = "sess-1"


class _Ports:
    """Records the command `launch_spec` hands to the container."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self._ids = ["tab-a", "tab-b"]
        self._secrets = ["ing-a", "side-a", "ing-b", "side-b"]

    def new_id(self) -> str:
        return self._ids.pop(0)

    def new_secret(self) -> str:
        return self._secrets.pop(0)

    def pause(self, seconds: float) -> None:
        _ = seconds

    def stop_process(self, pid: int) -> None:
        _ = pid

    def create_worktree(self, repo: str, tab_id: str, slug: str, base: str) -> tuple[str, str]:
        _ = (repo, slug, base)
        return f"/wt/{tab_id}", "main"

    def worktree_state(self, repo: str, dest: str) -> str:
        _ = (repo, dest)
        return "clean"

    def release_worktree(self, dest: str) -> None:
        _ = dest

    def remove_worktree(self, repo: str, dest: str, *, discard_ignored: bool) -> bool:
        _ = (repo, dest, discard_ignored)
        return True

    def ensure_container(self, name: str) -> None:
        _ = name

    def exec_argv(self, name: str, command: tuple[str, ...], env: dict[str, str], workdir: str) -> tuple[str, ...]:
        _ = (name, env, workdir)
        self.commands.append(command)
        return command

    def orphans(self, name: str, tab_id: str) -> tuple[int, ...]:
        _ = (name, tab_id)
        return ()

    def container_path(self, name: str) -> str:
        _ = name
        return "/usr/bin"

    def base_env(self) -> dict[str, str]:
        return {}

    def python_bin(self) -> str:
        return "/usr/bin/python3"

    def socket_path(self) -> str:
        return "/run/aconsole.sock"

    def ingest_port(self) -> str:
        return "8765"


def test_claude_sessions_lists_metadata_without_message_bodies(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    folder = home / ".claude" / "projects" / project_key(str(project))
    folder.mkdir(parents=True)
    _write(folder / "one.jsonl", "one", "first prompt line\nsecond prompt line", cwd=str(project), mtime=1_700_000_000)
    _write(folder / "two.jsonl", "two", "beta", cwd="/work", mtime=1_700_000_100, blocks=True)
    _write(folder / "three.jsonl", "three", "gamma", cwd="/work", mtime=1_700_000_200)
    assert project_key("/") == "-"
    listed = list_sessions(home, str(project))
    assert [item.session_id for item in listed.sessions] == ["three", "two", "one"]
    first = listed.sessions[2]
    assert first.preview == "first prompt line"
    assert first.title == "first prompt line"
    assert first.cwd == str(project)
    assert first.branch == "main"
    assert first.started_at == datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert listed.sessions[1].preview == "beta"
    fields = [(item.preview, item.title, item.cwd, item.branch) for item in listed.sessions]
    blob = json.dumps(fields)
    assert _HIDDEN not in blob
    assert list_sessions(home, "/missing").sessions == ()


def test_claude_sessions_resume_and_fork_are_in_launch_spec() -> None:
    ports = _Ports()
    service = TabService(FrozenClock(_WHEN), ports)
    service.add_project(
        ProjectSeed(project_id="proj", root="/repo", container="box", sessions=frozenset({_SESSION}))
    )
    opened = service.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.NEW_WORKTREE,
            resume_session=_SESSION,
            fork=True,
            title="chat",
        )
    )
    spec = service.launch_spec(TabIdParams(tab_id=TabId(str(opened.id))))
    expected = resume_argv(_SESSION, fork=True)
    assert ports.commands == [expected]
    assert spec.argv[-len(expected) :] == expected
    plain = service.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CODEX,
            workspace=WorkspaceMode.NEW_WORKTREE,
            title="other",
        )
    )
    codex = service.launch_spec(TabIdParams(tab_id=TabId(str(plain.id))))
    assert ports.commands[-1] == ("codex",)
    assert codex.argv[-1] == "codex"
    assert resume_argv(None, fork=False) == ("claude",)


def test_claude_sessions_skill_cache_drops_when_mtime_changes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "proj"
    user_skill = home / ".claude" / "skills" / "review" / "SKILL.md"
    project_skill = project / ".claude" / "skills" / "explain" / "SKILL.md"
    _skill(user_skill, "review", "Look at a diff")
    _skill(project_skill, "explain", "Explain this file")
    discovery = SkillDiscovery()
    first = discovery.discover(home, project)
    second = discovery.discover(home, project)
    assert second is first
    assert [item.kind for item in first] == [PaletteKind.NATIVE_SKILL, PaletteKind.NATIVE_SKILL]
    assert [item.title for item in first] == ["review", "explain"]
    assert [item.id for item in first] == ["native-skill:user:review", "native-skill:project:explain"]
    _skill(user_skill, "renamed", "Look at a diff")
    os.utime(user_skill, (1_800_000_000, 1_800_000_000))
    third = discovery.discover(home, project)
    assert third is not first
    assert third[0].title == "renamed"
    assert third[1].title == "explain"


def _write(path: Path, session_id: str, prompt: str, *, cwd: str, mtime: int, blocks: bool = False) -> None:
    content: str | list[dict[str, str]] = prompt
    if blocks:
        content = [{"type": "text", "text": prompt}]
    user = {
        "type": "user",
        "sessionId": session_id,
        "timestamp": "2026-10-27T12:00:00.000Z",
        "cwd": cwd,
        "gitBranch": "main",
        "message": {"role": "user", "content": content},
    }
    assistant = {"type": "assistant", "message": {"role": "assistant", "content": _HIDDEN}}
    path.write_text(json.dumps(user) + "\n" + json.dumps(assistant) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _skill(path: Path, name: str, description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n", encoding="utf-8")
