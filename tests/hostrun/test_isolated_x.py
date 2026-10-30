"""GUI tests run on a private X server (B1 request 2026-10-27-b1-gui-keyboard-display)."""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "hostrun"
sys.path.insert(0, str(TOOLS))

import actions  # noqa: E402
import isolated_x  # noqa: E402

# Writes its display number to -displayfd, records its argv, waits for SIGTERM.
FAKE_XWAYLAND = """#!{python}
import os, sys, time, json, pathlib
args = sys.argv[1:]
pathlib.Path({record!r}).write_text(json.dumps(args))
fd = int(args[args.index("-displayfd") + 1])
os.write(fd, args[0][1:].encode() + b"\\n")
os.close(fd)
time.sleep(60)
"""

# Records the environment it was given and exits 7.
CHILD = "import json, os, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(os.environ))); sys.exit(7)"


def _fake(tmp_path: Path) -> tuple[Path, Path]:
    record = tmp_path / "xwayland-args.json"
    fake = tmp_path / "Xwayland"
    fake.write_text(FAKE_XWAYLAND.format(python=sys.executable, record=str(record)), encoding="utf-8")
    fake.chmod(0o700)
    return fake, record


def test_gui_suite_runs_under_isolated_x(tmp_path: Path) -> None:
    repo = str(tmp_path)
    plan = actions.build_plan("test", ["gui", "-k", "test_b1_"], tmp_path, repo, 30)
    argv = plan.steps[0].argv
    assert argv[:3] == (sys.executable, str(TOOLS / "isolated_x.py"), "--")
    assert argv[3:] == (f"{repo}/.venv-ui/bin/python", "-m", "pytest", "-q", "-k", "test_b1_", *actions._SUITES["gui"])


def test_ui_suite_is_not_wrapped(tmp_path: Path) -> None:
    plan = actions.build_plan("test", ["ui"], tmp_path, str(tmp_path), 30)
    assert plan.steps[0].argv[0] == f"{tmp_path}/.venv-ui/bin/python"


def test_xauthority_is_one_wild_cookie_entry() -> None:
    data = isolated_x.xauthority(93, b"\x01" * 16)
    family, addr_len = struct.unpack(">HH", data[:4])
    assert family == 0xFFFF
    assert addr_len == 0
    assert b"\x00\x0293" in data
    assert data.endswith(b"\x00\x12MIT-MAGIC-COOKIE-1\x00\x10" + b"\x01" * 16)


def test_child_env_points_at_the_private_server(tmp_path: Path) -> None:
    env = isolated_x.child_env({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "HOME": "/h"}, 91, tmp_path / "a")
    assert env == {"DISPLAY": ":91", "XAUTHORITY": str(tmp_path / "a"), "GDK_BACKEND": "x11", "HOME": "/h"}


def test_run_starts_the_server_runs_the_command_and_cleans_up(tmp_path: Path) -> None:
    fake, record = _fake(tmp_path)
    seen = tmp_path / "child-env.json"
    base = {"PATH": os.environ["PATH"], "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(tmp_path)}
    code = isolated_x.run([sys.executable, "-c", CHILD, str(seen)], xwayland=str(fake), env=base)
    assert code == 7
    args = json.loads(record.read_text(encoding="utf-8"))
    child = json.loads(seen.read_text(encoding="utf-8"))
    assert args[0] == child["DISPLAY"]
    assert args[1:5] == ["-geometry", "1280x800", "-auth", child["XAUTHORITY"]]
    assert "-nolisten" in args
    assert child["GDK_BACKEND"] == "x11"
    assert "WAYLAND_DISPLAY" not in child
    assert not Path(child["XAUTHORITY"]).exists()


def test_run_refuses_without_a_wayland_session(tmp_path: Path) -> None:
    fake, record = _fake(tmp_path)
    assert isolated_x.run(["true"], xwayland=str(fake), env={"PATH": os.environ["PATH"]}) == 2
    assert not record.exists()


@pytest.mark.parametrize("argv", [[], ["true"], ["-x", "true"]])
def test_main_needs_a_double_dash(argv: list[str]) -> None:
    assert isolated_x.main(argv) == 2
