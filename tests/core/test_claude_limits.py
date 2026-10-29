"""Statusline windows become quota_updated, and two Claude accounts stay apart."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import msgspec

from aconsole.contracts.common import QuotaLevel
from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.infrastructure.agents.claude.limits import ClaudeLimits, SubprocessPort

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "claude" / "2.1.309"
_STATUSLINE = (
    b'{"session_id":"s-line","model":{"id":"opus","display_name":"Opus","vendor":"anthropic"},'
    b'"rate_limits":{"five_hour":{"used_percentage":12.5,"resets_at":1700000000,"note":"x"},'
    b'"seven_day":{"used_percentage":3,"resets_at":1700001000},"spare":true},"extra":1}'
)
_NOW = datetime(2023, 1, 1, tzinfo=UTC)
_RESET_5H = datetime.fromtimestamp(1700000000, tz=UTC)
_AUTH = ("claude", "auth", "status")


class _Fake:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...]) -> bytes:
        self.calls.append(argv)
        return self.stdout


def _account_id(token: bytes) -> str:
    return "claude:" + hashlib.sha256(token).hexdigest()[:12]


def _limits(stdout: bytes, board: QuotaAggregator | None = None) -> tuple[ClaudeLimits, _Fake, QuotaAggregator]:
    process = _Fake(stdout)
    aggregator = QuotaAggregator() if board is None else board
    return ClaudeLimits(process, aggregator, lambda: _NOW), process, aggregator


def test_claude_limits_statusline_updates_quota() -> None:
    auth = (_FIXTURES / "auth-status.json").read_bytes()
    limits, process, board = _limits(auth)
    event = limits.on_statusline("tab-1", _STATUSLINE)
    assert process.calls == [_AUTH]
    assert event is not None
    payload = msgspec.json.decode(msgspec.json.encode(event), type=dict[str, msgspec.Raw])
    assert msgspec.json.decode(payload["type"], type=str) == "quota_updated"
    account = event.account
    assert account.id == _account_id(auth)
    assert account.agent == "claude"
    assert account.plan_label == "Pro"
    assert len(account.quota) == 2
    five_hour, seven_day = account.quota
    assert five_hour.key == "5h"
    assert five_hour.used_pct == 12.5
    assert five_hour.resets_at == _RESET_5H
    assert five_hour.level is QuotaLevel.OK
    assert five_hour.observed_at == _NOW
    assert five_hour.estimated is False
    assert seven_day.key == "7d"
    assert seven_day.used_pct == 3.0
    assert seven_day.level is QuotaLevel.OK
    assert board.current(account.id, "5h") is not None
    assert board.current(account.id, "5h").used_pct == 12.5
    assert board.current(account.id, "7d") is not None
    assert board.current(account.id, "7d").used_pct == 3.0


def test_claude_limits_two_accounts_do_not_mix() -> None:
    board = QuotaAggregator()
    first = json.dumps({"loggedIn": True, "subscriptionType": "pro", "email": "one@example.com"}).encode()
    second = json.dumps({"loggedIn": True, "subscriptionType": "max", "email": "two@example.com"}).encode()
    later = (
        b'{"session_id":"s-b","rate_limits":{"five_hour":{"used_percentage":96,"resets_at":1700000000},'
        b'"seven_day":{"used_percentage":40,"resets_at":1700001000}}}'
    )
    limits_a, _, _ = _limits(first, board)
    limits_b, _, _ = _limits(second, board)
    event_a = limits_a.on_statusline("tab-a", _STATUSLINE)
    event_b = limits_b.on_statusline("tab-b", later)
    assert event_a is not None
    assert event_b is not None
    assert event_a.account.id == _account_id(b"one@example.com")
    assert event_b.account.id == _account_id(b"two@example.com")
    assert event_a.account.id != event_b.account.id
    assert event_a.account.plan_label == "Pro"
    assert event_b.account.plan_label == "Max"
    assert event_a.account.quota[0].used_pct == 12.5
    assert event_a.account.quota[0].level is QuotaLevel.OK
    assert event_b.account.quota[0].used_pct == 96.0
    assert event_b.account.quota[0].level is QuotaLevel.CRITICAL
    assert board.current(event_a.account.id, "5h").used_pct == 12.5
    assert board.current(event_b.account.id, "5h").used_pct == 96.0
    assert board.current(event_a.account.id, "7d").used_pct == 3.0
    assert board.current(event_b.account.id, "7d").used_pct == 40.0


def test_claude_limits_logged_out_and_empty_statusline_do_nothing() -> None:
    logged_out = b'{"loggedIn":false,"subscriptionType":"pro","email":"one@example.com"}'
    limits, process, board = _limits(logged_out)
    assert limits.on_statusline("tab-1", _STATUSLINE) is None
    assert process.calls == [_AUTH]
    assert board.current(_account_id(b"one@example.com"), "5h") is None
    quiet, quiet_process, _ = _limits(logged_out)
    assert quiet.on_statusline("tab-1", b'{"session_id":"s"}') is None
    assert quiet_process.calls == []


def test_claude_limits_subprocess_port(tmp_path: Path) -> None:
    auth = json.dumps({"loggedIn": True, "subscriptionType": "pro", "email": "one@example.com"}).encode()
    binary = tmp_path / "claude"
    binary.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write({auth!r})\n", encoding="utf-8")
    binary.chmod(0o755)
    port = SubprocessPort(str(binary))
    assert port.run(("claude", "auth", "login")) == b""
    assert port.run(_AUTH) == auth
    limits = ClaudeLimits(port, QuotaAggregator(), lambda: _NOW)
    event = limits.on_statusline("tab-1", _STATUSLINE)
    assert event is not None
    assert event.account.id == _account_id(b"one@example.com")
    missing = SubprocessPort(str(tmp_path / "missing"))
    assert missing.run(_AUTH) == b""
    refused = SubprocessPort("claude")
    assert refused.run(_AUTH) == b""
    hanging = tmp_path / "hang"
    hanging.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    hanging.chmod(0o755)
    assert SubprocessPort(str(hanging), timeout_s=0.05).run(_AUTH) == b""
    failing = tmp_path / "fail"
    failing.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n", encoding="utf-8")
    failing.chmod(0o755)
    assert SubprocessPort(str(failing)).run(_AUTH) == b""
