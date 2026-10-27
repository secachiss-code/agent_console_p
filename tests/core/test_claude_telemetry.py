"""Every A0.6 Claude fixture becomes a signal, and one bad body does not stop the consumer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.infrastructure.agents.claude.telemetry import read_claude
from aconsole.core.infrastructure.http.consumer import consume
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude" / "2.1.309"
_EVENTS = (
    "PermissionRequest",
    "UserPromptSubmit",
    "PostToolUse",
    "PreToolUse",
    "SessionEnd",
    "Stop",
)
_STATUS = {
    "PermissionRequest": TabStatus.WAITING_APPROVAL,
    "UserPromptSubmit": TabStatus.WORKING,
    "PostToolUse": TabStatus.WORKING,
    "PreToolUse": TabStatus.WORKING,
    "SessionEnd": TabStatus.IDLE,
    "Stop": TabStatus.IDLE,
}
_STATUSLINE = (
    b'{"session_id":"s-line","model":{"id":"opus","display_name":"Opus","vendor":"anthropic"},'
    b'"rate_limits":{"five_hour":{"used_percentage":12.5,"resets_at":1700000000,"note":"x"},'
    b'"seven_day":{"used_percentage":3,"resets_at":1700001000},"spare":true},"extra":1}'
)


def _event_of(name: str) -> str | None:
    if not name.endswith(".json"):
        return None
    for event in _EVENTS:
        if name.startswith(event):
            return event
    return None


def test_claude_telemetry_reads_every_a0_fixture() -> None:
    files = sorted(path for path in _FIXTURES.iterdir() if path.is_file())
    assert files
    for path in files:
        signal = read_claude(path.read_bytes(), kind="claude.hook")
        event = _event_of(path.name)
        if event is None:
            assert signal.unknown_events == 1
            assert signal.status is None
            continue
        assert signal.unknown_events == 0
        assert signal.event == event
        assert signal.status is _STATUS[event]
        assert signal.session_id
        assert signal.ignored_fields > 0
        assert signal.five_hour is None
        assert signal.seven_day is None


def test_claude_telemetry_counts_extra_fields() -> None:
    source = (_FIXTURES / "UserPromptSubmit.json").read_bytes()
    before = read_claude(source, kind="claude.hook")
    loaded = json.loads(source)
    assert isinstance(loaded, dict)
    loaded["extra_field"] = 1
    loaded["also_extra"] = {"z": True}
    after = read_claude(json.dumps(loaded).encode(), kind="claude.hook")
    assert after.ignored_fields == before.ignored_fields + 2
    assert after.status is TabStatus.WORKING
    assert after.session_id == before.session_id
    assert after.unknown_events == 0
    line = read_claude(_STATUSLINE, kind="claude.statusline")
    assert line.unknown_events == 0
    assert line.status is None
    assert line.session_id == "s-line"
    assert line.five_hour is not None
    assert line.five_hour.used_percentage == 12.5
    assert line.five_hour.resets_at == 1700000000
    assert line.seven_day is not None
    assert line.seven_day.used_percentage == 3.0
    assert line.ignored_fields == 4


def test_claude_telemetry_bad_record_does_not_stop_the_consumer() -> None:
    applied = asyncio.run(_apply_queue())
    assert applied == [("tab-1", "UserPromptSubmit"), ("tab-1", "Stop")]


async def _apply_queue() -> list[tuple[str, str]]:
    queue = IngestQueue()
    ready = asyncio.Event()
    applied: list[tuple[str, str]] = []

    async def apply(tab_id: str, name: str) -> None:
        applied.append((tab_id, name))
        if name == "Stop":
            ready.clear()

    prompt = (_FIXTURES / "UserPromptSubmit.json").read_bytes()
    queue.push(IngestItem(tab_id="tab-1", kind="claude.hook", body=b"not-json"))
    queue.push(IngestItem(tab_id="tab-1", kind="claude.hook", body=prompt))
    queue.push(IngestItem(tab_id="tab-1", kind="claude.statusline", body=b"[]"))
    queue.push(IngestItem(tab_id="tab-1", kind="codex.hook", body=b'{"session_id":"s","hook_event_name":"Stop"}'))
    ready.set()
    task = asyncio.create_task(consume(queue, ready, apply))
    for _ in range(50):
        if applied[-1:] == [("tab-1", "Stop")] and queue.depth == 0:
            break
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait([task])
    return applied
