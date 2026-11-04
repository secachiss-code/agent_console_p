"""What a tab's session has cost at API prices: Claude's statusline says it, OpenCode's stream is added up."""

from __future__ import annotations

import json

from aconsole.core.infrastructure.agents.opencode.stream import COST_EVENT, EventReader
from aconsole.core.infrastructure.http.consumer import hook_event, session_cost
from aconsole.core.infrastructure.http.routes import IngestItem


def _usage(session: str, cost: float) -> bytes:
    """OpenCode says each session's running total."""
    data = {"sessionID": session, "cost": cost, "tokens": {"input": 1, "output": 1}}
    return json.dumps({"id": "evt_1", "type": "session.usage.updated", "data": data}).encode()


def test_claude_statusline_carries_the_session_total() -> None:
    body = json.dumps({"session_id": "s", "cost": {"total_cost_usd": 1.2345, "total_duration_ms": 10}}).encode()
    assert session_cost(IngestItem(tab_id="t", kind="claude.statusline", body=body)) == 1.2345
    assert session_cost(IngestItem(tab_id="t", kind="claude.statusline", body=b'{"session_id": "s"}')) is None
    assert session_cost(IngestItem(tab_id="t", kind="claude.hook", body=body)) is None


def test_opencode_sessions_add_up_and_are_said_when_they_move() -> None:
    reader = EventReader()
    first = reader.read(_usage("root", 0.01))
    assert first is not None
    assert json.loads(first) == {"hook_event_name": COST_EVENT, "cost_usd": 0.01}
    # The same session goes on: its newest total replaces the old one, it is not added twice.
    again = reader.read(_usage("root", 0.03))
    assert again is not None
    assert json.loads(again)["cost_usd"] == 0.03
    subagent = reader.read(_usage("kid", 0.02))
    assert subagent is not None
    assert json.loads(subagent)["cost_usd"] == 0.05
    assert reader.read(_usage("kid", 0.0201)) is None, "a step under a twentieth of a cent is not news"


def test_a_deleted_session_keeps_its_cost_without_staying_in_the_map() -> None:
    reader = EventReader()
    assert reader.read(_usage("root", 0.01)) is not None
    assert reader.read(_usage("kid", 0.02)) is not None
    deleted = json.dumps({"type": "session.deleted", "data": {"sessionID": "kid"}}).encode()
    assert reader.read(deleted) is None
    moved = reader.read(_usage("root", 0.04))
    assert moved is not None
    assert json.loads(moved)["cost_usd"] == 0.06
    assert reader.read(_usage("kid", 9.0)) is None


def test_opencode_cost_map_keeps_a_bounded_number_of_sessions() -> None:
    reader = EventReader()
    last = b""
    for index in range(70):
        body = reader.read(_usage(f"s{index}", 0.01))
        if body is not None:
            last = body
    assert json.loads(last)["cost_usd"] == 0.7
    # The oldest session was folded in. A later total from it must not be added again.
    assert reader.read(_usage("s0", 5.0)) is None
    moved = reader.read(_usage("s69", 0.02))
    assert moved is not None
    assert json.loads(moved)["cost_usd"] == 0.71


def test_a_cost_signal_moves_no_status() -> None:
    body = json.dumps({"hook_event_name": COST_EVENT, "cost_usd": 0.4}).encode()
    item = IngestItem(tab_id="t", kind="opencode.event", body=body)
    assert hook_event(item) is None
    assert session_cost(item) == 0.4
