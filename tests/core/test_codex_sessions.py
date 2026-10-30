"""Codex sessions come from a read-only copy of state_5.sqlite, and rate limits become quota_updated."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest
from sqlalchemy import MetaData, insert
from sqlalchemy.exc import OperationalError

from aconsole.contracts.app import OpenTabParams, TabIdParams
from aconsole.contracts.common import AgentKind, ProjectId, QuotaLevel, TabId, WorkspaceMode
from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.application.services.tabs import ProjectSeed, TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.agents.codex.limits import CodexLimits
from aconsole.core.infrastructure.agents.codex.sessions import list_sessions, readonly_engine, resume_argv

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "0.164.0"
_SESSION = "01a18e40-592d-72a0-b435-d30b29521a9a"
_CWD = "/tmp/aconsole-a41"  # noqa: S108 -- cwd stored in the fixture row, not a file this test creates
_HIDDEN = "BODY-MUST-NOT-LEAK"
_WHEN = datetime(2026, 10, 29, tzinfo=UTC)
_NOW = datetime(2023, 1, 1, tzinfo=UTC)
_RESET_5H = datetime.fromtimestamp(1793405940, tz=UTC)
_RESET_7D = datetime.fromtimestamp(1793784697, tz=UTC)
_RESUME = "sess-1"


class _Ports:
    """Records the command `launch_spec` hands to the container."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self._ids = ["tab-a", "tab-b", "tab-c"]
        self._secrets = ["ing-a", "side-a", "ing-b", "side-b", "ing-c", "side-c"]

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


def _copy(tmp_path: Path) -> Path:
    copy = tmp_path / "state_5.sqlite"
    copy.write_bytes((_FIXTURES / "state_5.sqlite").read_bytes())
    os.utime(copy, (1_700_000_000, 1_700_000_000))
    rollout = tmp_path / "sessions" / "2026" / "10" / "24" / "rollout-probe.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(_HIDDEN, encoding="utf-8")
    return copy


def test_codex_sessions_lists_the_fixture_without_the_rollout_body(tmp_path: Path) -> None:
    copy = _copy(tmp_path)
    listed = list_sessions(copy, _CWD)
    assert [item.session_id for item in listed.sessions] == [_SESSION]
    info = listed.sessions[0]
    when = datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert info.title == "Probe thread"
    assert info.preview == "Probe thread"
    assert info.cwd == _CWD
    assert info.branch == "main"
    assert info.started_at == when
    assert info.updated_at == when
    blob = json.dumps((info.preview, info.title, info.cwd, info.branch))
    assert _HIDDEN not in blob
    assert list_sessions(copy, "/missing").sessions == ()
    assert list_sessions(tmp_path / "missing.sqlite", _CWD).sessions == ()


def test_codex_sessions_fixture_copy_cannot_be_written(tmp_path: Path) -> None:
    copy = _copy(tmp_path)
    before = copy.read_bytes()
    engine = readonly_engine(copy)
    metadata = MetaData()
    metadata.reflect(engine)
    threads = metadata.tables["threads"]
    with pytest.raises(OperationalError), engine.begin() as connection:
        _ = connection.execute(insert(threads).values(id="write-me"))
    engine.dispose()
    assert copy.read_bytes() == before
    assert [item.session_id for item in list_sessions(copy, _CWD).sessions] == [_SESSION]


def test_codex_sessions_resume_and_fork_are_in_launch_spec() -> None:
    ports = _Ports()
    service = TabService(FrozenClock(_WHEN), ports)
    service.add_project(ProjectSeed(project_id="proj", root="/repo", container="box", sessions=frozenset({_RESUME})))
    forked = service.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CODEX,
            workspace=WorkspaceMode.NEW_WORKTREE,
            resume_session=_RESUME,
            fork=True,
            title="chat",
        )
    )
    spec = service.launch_spec(TabIdParams(tab_id=TabId(str(forked.id))))
    expected = resume_argv(_RESUME, fork=True)
    assert ports.commands == [expected]
    assert spec.argv[-len(expected) :] == expected
    resumed = service.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CODEX,
            workspace=WorkspaceMode.NEW_WORKTREE,
            resume_session=_RESUME,
            title="again",
        )
    )
    again = service.launch_spec(TabIdParams(tab_id=TabId(str(resumed.id))))
    plain_resume = resume_argv(_RESUME, fork=False)
    assert ports.commands[-1] == plain_resume
    assert again.argv[-len(plain_resume) :] == plain_resume
    assert resume_argv(None, fork=False) == ("codex",)
    assert resume_argv("../" + _RESUME, fork=False) == ("codex",)


def test_codex_sessions_rate_limits_update_quota() -> None:
    body = (_FIXTURES / "rate-limits.jsonl").read_bytes().strip()
    limits = CodexLimits(QuotaAggregator(), lambda: _NOW)
    event = limits.on_rate_limits("tab-1", body)
    assert event is not None
    payload = msgspec.json.decode(msgspec.json.encode(event), type=dict[str, msgspec.Raw])
    assert msgspec.json.decode(payload["type"], type=str) == "quota_updated"
    account = event.account
    assert account.id == "codex:" + hashlib.sha256(b"redacted").hexdigest()[:12]
    assert account.agent == "codex"
    assert account.plan_label == "Plus"
    five_hour, seven_day = account.quota
    assert five_hour.key == "5h"
    assert five_hour.used_pct == 0.0
    assert isinstance(five_hour.used_pct, float)
    assert five_hour.resets_at == _RESET_5H
    assert five_hour.level is QuotaLevel.OK
    assert five_hour.observed_at == _NOW
    assert seven_day.key == "7d"
    assert seven_day.used_pct == 49.0
    assert isinstance(seven_day.used_pct, float)
    assert seven_day.resets_at == _RESET_7D
    assert limits.on_rate_limits("tab-1", b"{") is None


def test_codex_sessions_two_accounts_do_not_mix() -> None:
    board = QuotaAggregator()
    limits = CodexLimits(board, lambda: _NOW)
    first = _account_body("one@example.com", 12)
    second = _account_body("two@example.com", 96)
    one = limits.on_rate_limits("tab-1", first)
    two = limits.on_rate_limits("tab-2", second)
    assert one is not None
    assert two is not None
    assert one.account.id != two.account.id
    assert one.account.plan_label == "Plus"
    assert one.account.quota[0].used_pct == 12.0
    assert one.account.quota[0].level is QuotaLevel.OK
    assert two.account.quota[0].used_pct == 96.0
    assert two.account.quota[0].level is QuotaLevel.CRITICAL
    assert board.current(one.account.id, "5h") is not None
    assert board.current(one.account.id, "5h").used_pct == 12.0
    assert board.current(two.account.id, "5h") is not None
    assert board.current(two.account.id, "5h").used_pct == 96.0


def _account_body(email: str, used: int) -> bytes:
    return json.dumps(
        {
            "result": {
                "email": email,
                "rateLimits": {
                    "planType": "plus",
                    "primary": {"usedPercent": used, "windowDurationMins": 300, "resetsAt": 1700000000},
                },
            }
        }
    ).encode()
