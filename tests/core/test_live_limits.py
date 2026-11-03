"""The auth cache and the one-question exchange with the Codex app server."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aconsole.core.domain.quota import QuotaKey
from aconsole.core.infrastructure.agents.claude.limits import read_extra_usage, read_usage
from aconsole.core.infrastructure.agents.live_limits import CachedAuth, ask_rate_limits
from aconsole.core.infrastructure.containers.login import LOGIN_SHELL

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _script(tmp_path: Path, text: str) -> tuple[str, ...]:
    path = tmp_path / "server"
    _ = path.write_text(f"#!/bin/sh\n{text}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return (str(path),)


def test_auth_is_asked_once_and_a_failure_is_not_remembered() -> None:
    answers = [b"", b"{}", b"never"]
    calls: list[tuple[str, ...]] = []

    def source(argv: tuple[str, ...]) -> bytes:
        calls.append(argv)
        return answers[len(calls) - 1]

    auth = CachedAuth(source)
    assert auth.run(("a",)) == b""
    assert auth.run(("a",)) == b"{}"
    assert auth.run(("a",)) == b"{}"
    assert len(calls) == 2


def test_the_answer_is_found_among_other_lines(tmp_path: Path) -> None:
    text = """echo motd; echo '{"id":1}'; echo '{"method":"x"}'; echo '{"id":2,"result":{}}'; sleep 30"""
    assert ask_rate_limits(_script(tmp_path, text)) == b'{"id":2,"result":{}}'


def test_silence_and_a_missing_binary_give_nothing(tmp_path: Path) -> None:
    assert ask_rate_limits(_script(tmp_path, "sleep 30"), timeout_s=0.2) == b""
    assert ask_rate_limits(_script(tmp_path, "exit 3")) == b""
    assert ask_rate_limits((str(tmp_path / "missing"),)) == b""


_USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 51% used · resets Nov 3, 2:40am (Europe/Moscow)
Current week (all models): 19% used · resets Nov 6, 9pm (Europe/Moscow)

What's contributing to your usage?
"""


def test_usage_text_gives_percent_and_reset() -> None:
    now = datetime(2026, 11, 2, 21, 50, tzinfo=UTC)
    five, week = read_usage(_USAGE, now)
    assert five == (QuotaKey.FIVE_HOUR, 51.0, datetime(2026, 11, 2, 23, 40, tzinfo=UTC))
    assert week == (QuotaKey.SEVEN_DAY, 19.0, datetime(2026, 11, 6, 18, 0, tzinfo=UTC))


def test_usage_text_without_a_date_or_a_reset() -> None:
    now = datetime(2026, 12, 31, 20, 0, tzinfo=UTC)
    text = "Current session: 3% used · resets 2:40am (Europe/Moscow)\nCurrent week (all models): 0% used\n"
    five, week = read_usage(text, now)
    assert five[2] == datetime(2026, 12, 31, 23, 40, tzinfo=UTC)
    assert week == (QuotaKey.SEVEN_DAY, 0.0, None)
    assert read_usage("not logged in", now) == ()
    year_end = read_usage("Current session: 1% used · resets Jan 1, 1am (UTC)", now)
    assert year_end[0][2] == datetime(2027, 1, 1, 1, 0, tzinfo=UTC)


def test_a_profile_that_swallows_stdin_leaves_the_request_to_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distrobox's profile runs `host-spawn`, which reads stdin, when `podman exec` brings no XAUTHORITY."""
    for profile in (".profile", ".bash_profile", ".zprofile"):
        _ = (tmp_path / profile).write_text("cat > /dev/null\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    reply = '{"id":2,"result":{}}'
    answer = f"read -r first; read -r second; read -r third; case \"$third\" in *rateLimits*) echo '{reply}';; esac"
    argv = (*LOGIN_SHELL, "sh", "-c", answer)
    assert ask_rate_limits(argv) == reply.encode()


def test_extra_usage_is_a_budget_in_dollars() -> None:
    now = datetime(2026, 11, 2, 21, 50, tzinfo=UTC)
    text = _USAGE + "Extra usage: 77% used · $15.40 / $20.00 spent · resets Dec 1, 12am (UTC)\n"
    window = read_extra_usage(text, now)
    assert window is not None
    assert (window.key, window.used_pct, window.spent_usd, window.limit_usd) == ("budget", 77.0, 15.4, 20.0)
    assert window.resets_at == datetime(2026, 12, 1, tzinfo=UTC)
    # The interactive screen puts a bar and a comma in; the numbers are the same.
    drawn = read_extra_usage("Extra usage ██████ 7% used, $1,015.40 / $2,000.00 spent", now)
    assert drawn is not None
    assert (drawn.used_pct, drawn.spent_usd, drawn.limit_usd, drawn.resets_at) == (7.0, 1015.4, 2000.0, None)
    assert read_extra_usage(_USAGE, now) is None
    assert read_extra_usage("Extra usage not enabled • /extra-usage to enable", now) is None
