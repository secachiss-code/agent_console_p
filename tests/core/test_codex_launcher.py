"""Codex launch argv matches the snapshots, and untrusted hooks stay off."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab
from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache

_PROBES = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "0.164.0"
_EXPECTED = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "expected"
_MARKER = "tok" + "-secret"
_PORT = 8765
_TAB = "tab-1"
_USER_KEY = "/Users/me/.codex/config.toml:pre_tool_use:0:0"


def _tab(home: Path) -> CodexTab:
    return CodexTab(agent_home=home, tab_id=_TAB, port=_PORT, token=_MARKER)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _argv(name: str, home: Path) -> tuple[str, ...]:
    text = (_EXPECTED / name).read_text(encoding="utf-8").replace("__AGENT_HOME__", str(home))
    return tuple(text.splitlines())


def test_codex_launcher_probe_fixtures_cover_every_a0_probe() -> None:
    hooks = (_PROBES / "hooks-list.jsonl").read_text(encoding="utf-8")
    rollout = (_PROBES / "rollout.jsonl").read_text(encoding="utf-8")
    new = (_PROBES / "thread-new.jsonl").read_text(encoding="utf-8")
    clear = (_PROBES / "thread-clear.jsonl").read_text(encoding="utf-8")
    limits = (_PROBES / "rate-limits.jsonl").read_text(encoding="utf-8")
    assert "currentHash" in hooks
    assert "session_meta" in rollout
    assert "token_count" in rollout
    assert "sessionStartSource" not in new
    assert '"sessionStartSource":"clear"' in clear
    assert "rateLimits" in limits
    database = (_PROBES / "state_5.sqlite").read_bytes()
    schema = (_PROBES / "state_5.schema.sql").read_text(encoding="utf-8")
    assert database.startswith(b"SQLite format 3\x00")
    for column in ("id", "rollout_path", "cwd", "title", "tokens_used", "git_branch", "name"):
        assert column in schema


def test_codex_launcher_snapshot_matches_expected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text("keep = true\n", encoding="utf-8")
    trust = HookTrustCache()
    trust.load((_PROBES / "hooks-list.jsonl").read_bytes())
    launch = CodexLauncher().launch(_tab(home), trust)
    assert launch.argv == _argv("argv-trusted.txt", home)
    assert _MARKER not in launch.argv
    assert _MARKER.encode() not in launch.hook_path.read_bytes()
    assert _MARKER.encode() not in launch.notify_path.read_bytes()
    assert launch.hook_path.read_bytes() == (_EXPECTED / "aconsole-hook").read_bytes()
    assert launch.notify_path.read_bytes() == (_EXPECTED / "aconsole-notify").read_bytes()
    assert launch.env["ACONSOLE_TAB_TOKEN"] == _MARKER
    assert _mode(launch.hook_path) == 0o700
    assert _mode(launch.notify_path) == 0o700
    assert _mode(home / ".aconsole") == 0o700
    assert _mode(home / ".aconsole" / "bin") == 0o700
    assert config.read_text(encoding="utf-8") == "keep = true\n"
    assert not (home / "AGENTS.md").exists()
    bare = CodexLauncher().launch(_tab(home), HookTrustCache())
    assert bare.argv == _argv("argv-untrusted.txt", home)
    assert "hooks.PermissionRequest" not in "\n".join(bare.argv)


def test_codex_launcher_trust_cache_replaces_a_stale_hash() -> None:
    cache = HookTrustCache()
    cache.load((_PROBES / "hooks-list.jsonl").read_bytes())
    current = cache.hash_for(_USER_KEY)
    assert current is not None
    assert cache.accepts(_USER_KEY, current)
    cache.observe(_USER_KEY, "sha256:" + "ab" * 32)
    assert not cache.accepts(_USER_KEY, current)
    cache.load(b"not-json\n")
    assert cache.hash_for(_USER_KEY) == "sha256:" + "ab" * 32
    cache.observe("", current)
    cache.observe(_USER_KEY, "nope")
    assert cache.hash_for(_USER_KEY) == "sha256:" + "ab" * 32


def test_codex_launcher_rejects_bad_input(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    launcher = CodexLauncher()
    trust = HookTrustCache()
    with pytest.raises(ValueError, match="absolute"):
        launcher.launch(CodexTab(agent_home=Path("rel"), tab_id=_TAB, port=_PORT, token=_MARKER), trust)
    with pytest.raises(ValueError, match="tab id"):
        launcher.launch(CodexTab(agent_home=home, tab_id="", port=_PORT, token=_MARKER), trust)
    with pytest.raises(ValueError, match="port"):
        launcher.launch(CodexTab(agent_home=home, tab_id=_TAB, port=0, token=_MARKER), trust)
    with pytest.raises(ValueError, match="token"):
        launcher.launch(CodexTab(agent_home=home, tab_id=_TAB, port=_PORT, token=""), trust)
