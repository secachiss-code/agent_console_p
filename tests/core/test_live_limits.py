"""The auth cache and the one-question exchange with the Codex app server."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

from aconsole.core.infrastructure.agents.live_limits import CachedAuth, ask_rate_limits

if TYPE_CHECKING:
    from pathlib import Path


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
