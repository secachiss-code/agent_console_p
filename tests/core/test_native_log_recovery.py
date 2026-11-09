"""Native log corruption and rotation must not invent usage or expose transcript bodies."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
lazy from pathlib import Path

import pytest

from aconsole.core.infrastructure.agents.claude.sessions import list_sessions, project_key, read_session
from aconsole.core.infrastructure.agents.codex.rollout import RolloutTail, find_rollout, quotas
from aconsole.core.infrastructure.agents.usage_log import UsageEvent, UsageLog, claude_events, codex_events

_AT = datetime(2026, 11, 1, tzinfo=UTC)


def _reply(identity: str, at: datetime = _AT, **usage: object) -> bytes:
    return json.dumps({"type": "assistant", "timestamp": at.isoformat(), "requestId": identity,
                       "message": {"id": identity, "model": "claude-test", "usage": usage}}).encode()


def test_usage_window_can_widen_after_old_events_were_pruned(tmp_path: Path) -> None:
    path = tmp_path / "chat.jsonl"
    path.write_bytes(_reply("old", output_tokens=3) + b"\n"
                     + _reply("new", _AT + timedelta(days=1), output_tokens=7) + b"\n")
    log = UsageLog(lambda: tmp_path, claude_events)
    assert log.models(_AT)[0].output_tokens == 10
    assert log.models(_AT + timedelta(days=1))[0].output_tokens == 7
    assert log.models(_AT)[0].output_tokens == 10
    path.unlink()
    assert log.models(_AT) == ()


def test_usage_oversized_split_line_recovers_at_next_newline(tmp_path: Path) -> None:
    path = tmp_path / "chat.jsonl"
    # Exceed the public reader's 2 MiB per-line budget across several reads.
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    log = UsageLog(lambda: tmp_path, claude_events)
    assert log.models(_AT) == ()
    with path.open("ab") as stream:
        _ = stream.write(b"still not a record")
    assert log.models(_AT) == ()
    with path.open("ab") as stream:
        _ = stream.write(b"\n" + _reply("valid", output_tokens=9) + b"\n")
    assert log.models(_AT)[0].output_tokens == 9
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1) + b"\n" + _reply("replacement", output_tokens=4) + b"\n")
    assert log.models(_AT)[0].output_tokens == 4


def test_usage_revised_reply_replaces_prior_counts_and_rejects_invalid_counts(tmp_path: Path) -> None:
    path = tmp_path / "chat.jsonl"
    path.write_bytes(_reply("same", output_tokens=2) + b"\n"
                     + _reply("same", input_tokens=True, output_tokens=8,
                              cache_read_input_tokens=-1, cache_creation_input_tokens="bad") + b"\n")
    (usage,) = UsageLog(lambda: tmp_path, claude_events).models(_AT)
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (0, 8, 0, 0)


@pytest.mark.parametrize("line", [
    b"invalid", b"[]", b'{"type":"user","usage":{}}',
    b'{"type":"assistant","message":null,"usage":{}}',
    b'{"type":"assistant","message":{"model":"<synthetic>","usage":{}}}',
    b'{"type":"assistant","timestamp":"bad","message":{"model":"m","usage":{}}}',
    b'{"type":"assistant","timestamp":"2026-11-01","message":{"model":"m","usage":{}}}',
])
def test_corrupt_claude_usage_does_not_hide_a_following_valid_reply(tmp_path: Path, line: bytes) -> None:
    path = tmp_path / "chat.jsonl"
    path.write_bytes(line + b"\n" + _reply("valid", output_tokens=6) + b"\n")
    assert [event.output_tokens for event in claude_events(path)] == [6]
    assert UsageLog(lambda: tmp_path, claude_events).models(_AT)[0].output_tokens == 6


@pytest.mark.parametrize("bad", [
    {"type": "turn_context", "payload": None},
    {"type": "turn_context", "payload": {"model": 42}},
    {"type": "event_msg", "payload": {"type": "token_count"}},
    {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {}}}},
    {"type": "event_msg", "timestamp": "bad", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"total_tokens": 4}, "last_token_usage": {"output_tokens": 4}}}},
])
def test_corrupt_codex_records_preserve_model_and_next_valid_count(tmp_path: Path, bad: dict[str, object]) -> None:
    path = tmp_path / "rollout.jsonl"
    context = {"type": "turn_context", "payload": {"model": "codex-test"}}
    event = {"type": "event_msg", "timestamp": _AT.isoformat(), "payload": {"type": "token_count", "info": {
        "total_token_usage": {"total_tokens": 10},
        "last_token_usage": {"input_tokens": 3, "cached_input_tokens": 8, "output_tokens": 2}}}}
    path.write_bytes(b"\n".join(json.dumps(item).encode() for item in (context, bad, event)) + b"\n")
    (usage,) = UsageLog(lambda: tmp_path, codex_events).models(_AT)
    assert usage.model == "codex-test"
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (0, 8, 2)
    assert len(tuple(codex_events(path))) == 1


def test_custom_usage_reader_skips_unavailable_file_and_counts_healthy_sibling(tmp_path: Path) -> None:
    path = tmp_path / "custom.jsonl"
    path.touch()
    event = UsageEvent(key=None, at=_AT, model="custom", input_tokens=1, output_tokens=2,
                       cache_read_tokens=3, cache_write_tokens=4)
    (tmp_path / "unavailable.jsonl").touch()

    def read(source: Path) -> list[UsageEvent]:
        if source.name == "unavailable.jsonl":
            raise PermissionError
        return [event]

    log = UsageLog(lambda: tmp_path, read)
    (usage,) = log.models(_AT)
    assert usage.model == "custom"
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (1, 2, 3, 4)


def test_disabling_usage_source_discards_cached_counts(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(_reply("valid", output_tokens=5) + b"\n")
    source: list[Path | None] = [tmp_path]
    log = UsageLog(lambda: source[0], claude_events)
    assert log.models(_AT)[0].output_tokens == 5
    source[0] = None
    assert log.models(_AT) == ()
    path.write_bytes(_reply("replacement", output_tokens=2) + b"\n")
    source[0] = tmp_path
    assert log.models(_AT)[0].output_tokens == 2


def test_old_log_is_not_counted_until_its_window_is_requested(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_bytes(_reply("old", output_tokens=5) + b"\n")
    os.utime(path, (_AT.timestamp(), _AT.timestamp()))
    log = UsageLog(lambda: tmp_path, claude_events)
    assert log.models(_AT + timedelta(days=1)) == ()
    assert log.models(_AT)[0].output_tokens == 5


def test_claude_metadata_skips_noise_and_uses_role_without_leaking_body(tmp_path: Path) -> None:
    folder = tmp_path / ".claude/projects" / project_key("/repo")
    folder.mkdir(parents=True)
    path = folder / "fallback.jsonl"
    records = [
        {"type": "progress", "session_id": "restored", "timestamp": "bad", "cwd": 5, "git_branch": " work "},
        {"timestamp": "2026-11-01T12:00:00", "message": {"role": "user", "content": [
            {"type": "image"}, {"text": " first prompt\nSECRET SECOND LINE"}]}},
    ]
    path.write_text('\nnot-json\n[]\n{"type":"assistant","message":"SECRET BODY"}\n'
                    + "\n".join(json.dumps(record) for record in records), encoding="utf-8")
    (info,) = list_sessions(tmp_path, "/repo").sessions
    assert (info.session_id, info.branch, info.cwd, info.title, info.preview) == (
        "restored", "work", "", "first prompt", "first prompt")
    assert info.started_at == datetime(2026, 11, 1, 12, tzinfo=UTC)
    link = folder / "link.jsonl"
    link.symlink_to(path)
    assert read_session(link) is None
    assert read_session(folder / "missing.jsonl") is None
    assert len(list_sessions(tmp_path, "/repo").sessions) == 1


@pytest.mark.parametrize("message", [None, [], {}, {"content": None}, {"content": 42},
                                      {"content": [{}]}, {"content": "\nsecret"}])
def test_claude_metadata_without_a_preview_falls_back_to_session_id(tmp_path: Path, message: object) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "user", "message": message}), encoding="utf-8")
    info = read_session(path)
    assert info is not None
    assert info.title == "session"
    assert info.preview is None
    assert info.started_at == info.updated_at


def test_claude_metadata_read_budget_ignores_late_prompt(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n" * 32 + json.dumps({"type": "user", "message": {"content": "too late"}}),
                    encoding="utf-8")
    info = read_session(path)
    assert info is not None
    assert info.title == "session"
    assert info.preview is None


def test_claude_project_symlink_does_not_discover_foreign_sessions(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "session.jsonl").write_text('{"type":"user","message":{"content":"foreign"}}', encoding="utf-8")
    projects = tmp_path / ".claude/projects"
    projects.mkdir(parents=True)
    (projects / project_key("/repo")).symlink_to(foreign, target_is_directory=True)
    assert list_sessions(tmp_path, "/repo").sessions == ()


def test_claude_first_prompt_is_bounded_and_later_messages_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps({"type": "user", "timestamp": "2026-11-01T12:00:00Z",
                                "message": {"content": "a" * 300 + "\nsecret"}})
                    + '\n{"type":"user","message":{"content":"second"}}', encoding="utf-8")
    info = read_session(path)
    assert info is not None
    assert info.title == info.preview == "a" * 200
    assert info.started_at == datetime(2026, 11, 1, 12, tzinfo=UTC)


def test_rollout_disappearance_resets_offset_and_rejects_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "rollout-1-session.jsonl"
    path.write_bytes(b'\n{"first":1}\ncut')
    tail = RolloutTail(tmp_path)
    assert tail.read("session").lines == (b'{"first":1}',)
    path.unlink()
    assert tail.read("session").path is None
    assert tail.read("session").offset == 0
    path.write_bytes(b'{"new":2}\n')
    assert tail.read("session").lines == (b'{"new":2}',)
    alias = tmp_path / "rollout-2-other.jsonl"
    alias.symlink_to(path)
    assert find_rollout(tmp_path, "other") is None
    assert find_rollout(tmp_path, "") is None
    assert find_rollout(tmp_path, "bad\\id") is None


@pytest.mark.parametrize("window", [None, [], {}, {"window_minutes": True},
    {"window_minutes": 300, "used_percent": "bad", "resets_at": 3},
    {"window_minutes": 60, "used_percent": 20, "resets_at": 3},
    {"window_minutes": 300, "used_percentage": 20.5, "resets_at": "bad"}])
def test_bad_quota_window_does_not_hide_valid_sibling(window: object) -> None:
    line = json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "primary": window, "secondary": {"window_minutes": 10080, "used_percentage": 20.5, "resets_at": 3}}}})
    (valid,) = quotas(line.encode())
    assert valid.used_pct == 20.5
    assert valid.resets_at == 3
