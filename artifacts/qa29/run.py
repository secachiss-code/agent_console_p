"""Finite focused lifecycle/regression attempts and one original scale module."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa29"
sys.path.insert(0, str(ROOT / "tools"))
from native_ui_tests import run_tests  # noqa: E402

if sys.argv[1:] not in (["focused"], ["focused-fixed"], ["scale"]):
    raise SystemExit("usage: run.py focused|focused-fixed|scale")
mode = sys.argv[1]
output = OUT / mode
output.mkdir(exist_ok=False)
(output / "environment.json").write_text(json.dumps({
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "diff": subprocess.check_output(["git", "diff", "--", "tests/gui/front"], cwd=ROOT, text=True),
    "environment": {key: os.environ.get(key) for key in (
        "DISPLAY", "GDK_BACKEND", "GSK_RENDERER", "GTK_A11Y", "XDG_SESSION_TYPE",
        "LIBGL_ALWAYS_SOFTWARE", "MESA_LOADER_DRIVER_OVERRIDE")},
}, indent=2) + "\n")
env = dict(os.environ, GDK_BACKEND="x11", GTK_A11Y="test", PYTHONPATH=str(ROOT / "src"),
           ACONSOLE_SCALE_REPORT=str(output / "scale.txt"))
env.pop("WAYLAND_DISPLAY", None)
env.pop("BROADWAY_DISPLAY", None)
targets = (["tests/gui/front/test_css_lifecycle.py", "tests/gui/front/test_contrast_table.py",
            "tests/gui/front/test_git_pane.py", "tests/gui/front/test_git_actions.py",
            "tests/gui/front/test_git_history_keyboard.py", "tests/gui/front/test_git_truth.py",
            "tests/gui/front/test_work_sessions.py"] if mode.startswith("focused")
           else ["tests/gui/front/test_git_scale_ui.py"])
sys.exit(run_tests(env, targets, output, 180))
