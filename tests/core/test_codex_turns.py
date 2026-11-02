"""A Codex turn seen through its session file."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from aconsole.core.infrastructure.agents.codex.turns import TurnWatch

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
    assert watch.changes(_NOW) == ()
    with session.open("a", encoding="utf-8") as file:
        _ = file.write(_line("event_msg", "task_complete"))
    assert watch.changes(_NOW) == (("/work", False),)
