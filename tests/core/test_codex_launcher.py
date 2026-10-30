"""Codex launch argv matches the snapshots, and untrusted hooks stay off."""

from __future__ import annotations

import re
import stat
from pathlib import Path

import msgspec
import pytest

from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab, thread_start_request
from aconsole.core.infrastructure.agents.codex.trust import HookTrustCache

_PROBES = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "0.164.0"
_EXPECTED = Path(__file__).resolve().parents[1] / "fixtures" / "codex" / "expected"
_MARKER = "tok" + "-secret"
_PORT = 8765
_TAB = "tab-1"
_PRE_TOOL = "/<session-flags>/config.toml:pre_tool_use:0:0"
_PROBE_COMMAND = "/home/sas/.containers/containers/agent-codex/.aconsole/bin/aconsole-hook codex"
_CWD = "/tmp/aconsole-a41"  # noqa: S108 -- cwd recorded in the live probe, not a directory this test creates
_THREAD_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STATE_PAIR = re.compile(r'"([^"]+)" = \{ trusted_hash = "(sha256:[0-9a-f]{64})" \}')


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
    assert "sessionStartSource" not in clear
    assert _without_ids(new) == _without_ids(clear)
    fresh = thread_start_request(cwd=_CWD, request_id=4, clear=False)
    cleared = thread_start_request(cwd=_CWD, request_id=5, clear=True)
    assert fresh.method == "thread/start"
    assert fresh.params.cwd == _CWD
    assert fresh.params.session_start_source is None
    assert cleared.params.session_start_source == "clear"
    assert b"sessionStartSource" not in msgspec.json.encode(fresh)
    assert b'"sessionStartSource":"clear"' in msgspec.json.encode(cleared)
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
    current = cache.hash_for(_PRE_TOOL)
    assert current == "sha256:48b8c28ff5598772d3ee78c3462f10dadb1d57f9cd4246ed382539065273dc20"
    assert cache.event_name(_PRE_TOOL) == "preToolUse"
    assert cache.accepts(_PRE_TOOL, current)
    cache.observe(_PRE_TOOL, "sha256:" + "ab" * 32)
    assert not cache.accepts(_PRE_TOOL, current)
    cache.load(b"not-json\n")
    assert cache.hash_for(_PRE_TOOL) == "sha256:" + "ab" * 32
    cache.observe("", current)
    cache.observe(_PRE_TOOL, "nope")
    assert cache.hash_for(_PRE_TOOL) == "sha256:" + "ab" * 32


def test_codex_launcher_live_hooks_state_matches_the_probe(tmp_path: Path) -> None:
    raw = (_PROBES / "hooks-list.jsonl").read_bytes()
    listed = _listed_hooks(raw)
    assert len(listed) == 7
    assert {hook.command for hook in listed} == {_PROBE_COMMAND}
    hashes = [hook.current_hash for hook in listed]
    assert len(set(hashes)) == 7
    trust = HookTrustCache()
    trust.load(raw)
    for hook in listed:
        assert hook.key.startswith("/<session-flags>/config.toml:")
        assert hook.key.endswith(":0:0")
        assert "_" not in hook.event_name
        assert hook.event_name[:1].islower()
        assert trust.hash_for(hook.key) == hook.current_hash
        assert trust.event_name(hook.key) == hook.event_name
    home = tmp_path / "home"
    home.mkdir()
    launch = CodexLauncher().launch(_tab(home), trust)
    command = f"{launch.hook_path} codex"
    assert command.endswith("/.aconsole/bin/aconsole-hook codex")
    assert _state_map(launch.argv) == {hook.key: hook.current_hash for hook in listed}
    assert "aconsole:" not in "\n".join(launch.argv)
    end = next(item for item in launch.argv if item.startswith("hooks.SessionEnd="))
    assert f'command="{command}"' in end
    assert end.endswith("timeout=3}]}]")
    trusted = _listed_hooks((_PROBES / "hooks-list-trusted.jsonl").read_bytes())
    assert len(trusted) == 7
    assert {hook.trust_status for hook in trusted} == {"trusted"}
    assert {hook.key: hook.current_hash for hook in trusted} == {hook.key: hook.current_hash for hook in listed}


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


class _ListedHook(msgspec.Struct, frozen=True, rename="camel"):
    key: str
    event_name: str
    command: str
    current_hash: str
    trust_status: str


class _HookGroup(msgspec.Struct, frozen=True):
    hooks: list[_ListedHook]


class _HookListResult(msgspec.Struct, frozen=True):
    data: list[_HookGroup]


class _HookListEnvelope(msgspec.Struct, frozen=True):
    result: _HookListResult


def _listed_hooks(raw: bytes) -> tuple[_ListedHook, ...]:
    envelope = msgspec.json.decode(raw, type=_HookListEnvelope)
    return tuple(hook for group in envelope.result.data for hook in group.hooks)


def _state_map(argv: tuple[str, ...]) -> dict[str, str]:
    line = next(item for item in argv if item.startswith("hooks.state="))
    return dict(_STATE_PAIR.findall(line))


def _without_ids(text: str) -> str:
    """Drop the JSON-RPC id and the thread ids. The two live responses then match."""
    return re.sub(r'"id":\d+', '"id":0', _THREAD_ID.sub("ID", text), count=1)
