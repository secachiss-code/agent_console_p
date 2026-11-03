"""What a tab's session has cost at API prices: Claude's statusline says it, OpenCode's stream is added up."""

from __future__ import annotations

import json

from aconsole.core.infrastructure.agents.opencode.stream import COST_EVENT, EventReader
from aconsole.core.infrastructure.http.consumer import hook_event, session_cost
from aconsole.core.infrastructure.http.routes import IngestItem


def _message(message: str, cost: float, role: str = "assistant") -> bytes:
    info = {"id": message, "sessionID": "s1", "role": role, "cost": cost}
    return json.dumps({"type": "message.updated", "properties": {"info": info}}).encode()


def test_claude_statusline_carries_the_session_total() -> None:
    body = json.dumps({"session_id": "s", "cost": {"total_cost_usd": 1.2345, "total_duration_ms": 10}}).encode()
    assert session_cost(IngestItem(tab_id="t", kind="claude.statusline", body=body)) == 1.2345
    assert session_cost(IngestItem(tab_id="t", kind="claude.statusline", body=b'{"session_id": "s"}')) is None
    assert session_cost(IngestItem(tab_id="t", kind="claude.hook", body=body)) is None


def test_opencode_messages_add_up_and_are_said_when_they_move() -> None:
    reader = EventReader()
    first = reader.read(_message("m1", 0.01))
    assert first is not None
    assert json.loads(first) == {"hook_event_name": COST_EVENT, "cost_usd": 0.01}
    # The same message streams on: its newest cost replaces the old one, it is not added twice.
    again = reader.read(_message("m1", 0.03))
    assert again is not None
    assert json.loads(again)["cost_usd"] == 0.03
    second = reader.read(_message("m2", 0.02))
    assert second is not None
    assert json.loads(second)["cost_usd"] == 0.05
    assert reader.read(_message("m2", 0.0201)) is None, "a step under a twentieth of a cent is not news"
    assert reader.read(_message("u1", 0.5, role="user")) is None


def test_a_cost_signal_moves_no_status() -> None:
    body = json.dumps({"hook_event_name": COST_EVENT, "cost_usd": 0.4}).encode()
    item = IngestItem(tab_id="t", kind="opencode.event", body=body)
    assert hook_event(item) is None
    assert session_cost(item) == 0.4
