"""Finite desktop scale run with explicit GL/WM and native renderer evidence."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa28"
sys.path.insert(0, str(ROOT / "tools"))
from native_ui_tests import run_tests  # noqa: E402 - repository runner import

if sys.argv[1:] not in ([], ["--full"]):
    raise SystemExit("usage: desktop.py [--full]")
full = sys.argv[1:] == ["--full"]
output = OUT / ("desktop-full" if full else "desktop-scale")
output.mkdir(exist_ok=False)
observations = {
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "environment": {key: os.environ.get(key) for key in (
        "DISPLAY", "GSK_RENDERER", "GDK_BACKEND", "GTK_A11Y", "XDG_SESSION_TYPE",
        "LIBGL_ALWAYS_SOFTWARE", "MESA_LOADER_DRIVER_OVERRIDE",
    )},
}
for name, arguments in [("glxinfo", ["-B"]), ("xprop", ["-root", "_NET_SUPPORTING_WM_CHECK"])]:
    binary = shutil.which(name)
    if binary is None:
        observations[name] = {"available": False}
        continue
    result = subprocess.run([binary, *arguments], capture_output=True, text=True, timeout=15)
    observations[name] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    if name == "xprop" and result.returncode == 0:
        identity = result.stdout.rsplit(" ", 1)[-1].strip()
        if identity.startswith("0x"):
            result = subprocess.run(
                [binary, "-id", identity, "_NET_WM_NAME"], capture_output=True, text=True, timeout=15
            )
            observations["wm_name"] = {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
(output / "environment.json").write_text(json.dumps(observations, indent=2) + "\n")
env = dict(
    os.environ,
    GDK_BACKEND="x11",
    GTK_A11Y="test",
    PYTHONPATH=str(ROOT / "src") + ":" + str(OUT),
    QA28_OBSERVATION=str(output / "observation.json"),
    ACONSOLE_SCALE_REPORT=str(output / "scale.txt"),
)
env.pop("WAYLAND_DISPLAY", None)
env.pop("BROADWAY_DISPLAY", None)
targets = (
    ["tests/gui", "tests/ui", "tests/smoke"]
    if full
    else ["tests/gui/front/test_git_scale_ui.py::test_scrolling_a_long_history"]
)
status = run_tests(env, ["-p", "observe", *targets], output, 300)
sys.exit(status)
