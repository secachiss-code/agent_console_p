"""An agent starts the same way however a machine set up its container."""

from __future__ import annotations

import os
import pty
import stat
import subprocess
from pathlib import Path

import pytest

from aconsole.core.infrastructure.containers.login import LOGIN_SHELL
from aconsole.core.infrastructure.host_bins import host_binary

_PROFILES = (".profile", ".bash_profile", ".zprofile")
_MISSING = 127


def _agent(directory: Path, text: str = "echo ran $*") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "some-agent"
    _ = path.write_text(f"#!/bin/sh\n{text}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _profile(home: Path, text: str) -> None:
    for name in _PROFILES:
        _ = (home / name).write_text(f"{text}\n", encoding="utf-8")


def _run(home: Path, *command: str, stdin: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "USER": os.environ.get("USER", "")}
    return subprocess.run(
        (*LOGIN_SHELL, *command), env=env, input=stdin, capture_output=True, check=False, timeout=60,
    )


@pytest.mark.parametrize("place", [".npm-global/bin", ".local/bin", "bin", ".cargo/bin"])
def test_finds_an_agent_the_profile_does_not_know(tmp_path: Path, place: str) -> None:
    _agent(tmp_path / place)
    # The fault this was written for: a profile that puts `/.npm-global/bin` on PATH.
    _profile(tmp_path, 'export PATH="${UNSET_VARIABLE}/.npm-global/bin:$PATH"')
    result = _run(tmp_path, "some-agent", "a b")
    assert result.stdout.strip() == b"ran a b"


def test_reads_the_npm_prefix(tmp_path: Path) -> None:
    prefix = tmp_path / "elsewhere"
    _agent(prefix / "bin")
    _ = (tmp_path / ".npmrc").write_text(f"prefix={prefix}\n", encoding="utf-8")
    assert _run(tmp_path, "some-agent").stdout.strip() == b"ran"


def test_the_profile_wins_over_the_usual_places(tmp_path: Path) -> None:
    _agent(tmp_path / ".local/bin", "echo fallback")
    _agent(tmp_path / "chosen", "echo chosen")
    _profile(tmp_path, f'export PATH="{tmp_path}/chosen:$PATH"')
    assert _run(tmp_path, "some-agent").stdout.strip() == b"chosen"


def test_finds_tools_beside_the_container_homes(tmp_path: Path) -> None:
    """Codex is a Node script: it also needs `node` from the shared `host-bins`."""
    home = tmp_path / ".containers" / "containers" / "agent-x"
    home.mkdir(parents=True)
    _agent(tmp_path / ".containers" / "host-bins")
    assert _run(home, "some-agent").stdout.strip() == b"ran"


def test_a_profile_cannot_swallow_stdin(tmp_path: Path) -> None:
    _profile(tmp_path, "cat > /dev/null")
    assert _run(tmp_path, "cat", stdin=b"request\n").stdout == b"request\n"


def test_a_terminal_stays_the_agent_terminal(tmp_path: Path) -> None:
    _profile(tmp_path, "cat > /dev/null")
    primary, secondary = pty.openpty()
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "USER": os.environ.get("USER", "")}
    try:
        result = subprocess.run(
            (*LOGIN_SHELL, "sh", "-c", "[ -t 0 ] && echo terminal"),
            env=env, stdin=secondary, capture_output=True, check=False, timeout=60,
        )
    finally:
        os.close(primary)
        os.close(secondary)
    assert result.stdout.strip() == b"terminal"


def test_a_missing_agent_says_what_was_tried(tmp_path: Path) -> None:
    result = _run(tmp_path, "some-agent")
    assert result.returncode == _MISSING
    assert b"some-agent is not installed" in result.stderr
    assert str(tmp_path / ".npm-global/bin").encode() in result.stderr


def test_a_host_tool_comes_from_the_system_before_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _agent(tmp_path)
    (tmp_path / "some-agent").rename(tmp_path / "sh")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert host_binary("sh") != tmp_path / "sh"


def test_a_host_tool_outside_the_system_comes_from_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _agent(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert host_binary("some-agent") == tmp_path / "some-agent"
    assert host_binary("no-such-tool") == Path("/usr/bin/no-such-tool")
