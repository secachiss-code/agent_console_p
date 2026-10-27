"""Claude tab files match the committed etalons and never receive the tab token."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab

_EXPECTED = Path(__file__).resolve().parents[1] / "fixtures" / "claude" / "expected"
_MARKER = "tok" + "-secret"
_USER = b'{"keep":true}\n'
_PORT = 8765
_TAB = "tab-1"


def _tab(home: Path, *, tab_id: str = _TAB, port: int = _PORT, token: str = _MARKER) -> ClaudeTab:
    return ClaudeTab(agent_home=home, tab_id=tab_id, port=port, token=token)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_claude_launcher_snapshot_matches_expected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    user = home / ".claude"
    user.mkdir()
    user_settings = user / "settings.json"
    user_settings.write_bytes(_USER)
    launch = ClaudeLauncher().launch(_tab(home))
    settings = launch.settings_path
    script = launch.script_path
    expected_settings = (_EXPECTED / "settings.json").read_bytes().replace(b"__AGENT_HOME__", os.fsencode(home))
    assert settings.read_bytes() == expected_settings
    assert script.read_bytes() == (_EXPECTED / "statusline.sh").read_bytes()
    assert _MARKER.encode() not in settings.read_bytes()
    assert _MARKER.encode() not in script.read_bytes()
    assert launch.argv == ("claude", "--settings", str(settings))
    assert _MARKER not in launch.argv
    assert launch.env == {"ACONSOLE_PORT": str(_PORT), "ACONSOLE_TAB": _TAB, "ACONSOLE_TAB_TOKEN": _MARKER}
    assert _mode(settings) == 0o600
    assert _mode(script) == 0o700
    root = home / ".aconsole"
    for directory in (root, root / "run", root / "run" / _TAB, root / "bin"):
        assert _mode(directory) == 0o700
    assert list(root.rglob(".aconsole-*")) == []
    assert user_settings.read_bytes() == _USER
    again = ClaudeLauncher().launch(_tab(home))
    assert again.settings_path.read_bytes() == expected_settings
    assert again.script_path.read_bytes() == script.read_bytes()


def test_claude_launcher_close_removes_only_the_tab_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    user_settings = home / ".claude" / "settings.json"
    user_settings.parent.mkdir()
    user_settings.write_bytes(_USER)
    launcher = ClaudeLauncher()
    first = launcher.launch(_tab(home))
    second = launcher.launch(_tab(home, tab_id="tab-2"))
    launcher.close(home, _TAB)
    assert not first.settings_path.parent.exists()
    assert second.settings_path.is_file()
    assert first.script_path.is_file()
    assert user_settings.read_bytes() == _USER
    launcher.close(home, _TAB)
    assert second.settings_path.is_file()
    launcher.close(home, "tab-2")
    assert not second.settings_path.parent.exists()
    assert first.script_path.is_file()


def test_claude_launcher_rejects_bad_input(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    launcher = ClaudeLauncher()
    with pytest.raises(ValueError, match="absolute"):
        launcher.launch(_tab(Path("relative")))
    with pytest.raises(ValueError, match="directory"):
        launcher.launch(_tab(tmp_path / "missing"))
    file_home = tmp_path / "file-home"
    file_home.write_bytes(b"x")
    with pytest.raises(ValueError, match="directory"):
        launcher.launch(_tab(file_home))
    linked = tmp_path / "linked-home"
    linked.symlink_to(home)
    with pytest.raises(ValueError, match="directory"):
        launcher.launch(_tab(linked))
    for tab_id in ("", "..", "a/b", "x" * 65):
        with pytest.raises(ValueError, match="tab id"):
            launcher.launch(_tab(home, tab_id=tab_id))
    for port in (0, 65536, True):
        with pytest.raises(ValueError, match="port"):
            launcher.launch(_tab(home, port=port))
    with pytest.raises(ValueError, match="token"):
        launcher.launch(_tab(home, token=""))
    assert not (home / ".aconsole").exists()


def test_claude_launcher_refuses_symlinks(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_bytes(b"k\n")
    run = home / ".aconsole" / "run"
    run.mkdir(parents=True)
    (run / _TAB).symlink_to(outside, target_is_directory=True)
    launcher = ClaudeLauncher()
    with pytest.raises(ValueError, match="symlink"):
        launcher.launch(_tab(home))
    assert keep.read_bytes() == b"k\n"
    with pytest.raises(ValueError, match="symlink"):
        launcher.close(home, _TAB)
    assert keep.is_file()
    (run / _TAB).unlink()
    tab_dir = run / _TAB
    tab_dir.mkdir()
    target = tmp_path / "settings-target.json"
    target.write_bytes(b"safe\n")
    (tab_dir / "settings.json").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        launcher.launch(_tab(home))
    assert target.read_bytes() == b"safe\n"


def test_claude_launcher_refuses_a_file_where_a_directory_belongs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    blocked = home / ".aconsole"
    blocked.write_bytes(b"not-a-dir")
    with pytest.raises(ValueError, match="not a directory"):
        ClaudeLauncher().launch(_tab(home))
    assert blocked.read_bytes() == b"not-a-dir"
    blocked.unlink()
    tab_file = home / ".aconsole" / "run" / _TAB
    tab_file.parent.mkdir(parents=True)
    tab_file.write_bytes(b"file\n")
    with pytest.raises(ValueError, match="not a directory"):
        ClaudeLauncher().close(home, _TAB)
    assert tab_file.read_bytes() == b"file\n"
