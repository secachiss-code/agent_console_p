"""Own the display used for three policy explanation snapshots."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from native_ui_tests import private_display  # noqa: E402

OUT = ROOT / "artifacts/apr7/ui"
OUT.mkdir(parents=True, exist_ok=True)
with private_display(str(ROOT / "artifacts/qa32/xvfb-package/extracted/usr/bin/Xvfb"), OUT) as display:
    env = dict(os.environ, **display, GDK_BACKEND="x11", GTK_A11Y="test", PYTHONPATH=str(ROOT / "src"))
    env.pop("WAYLAND_DISPLAY", None)
    for theme, size, name in [("light", "1280x800", "light"), ("dark", "1280x800", "dark"),
                               ("light", "700x800", "narrow")]:
        subprocess.run([sys.executable, str(ROOT / "artifacts/apr7/policy_preview.py"), "--scenario", "demo",
                        "--view", "prefs", "--theme", theme, "--size", size, "--out", str(OUT / f"{name}.png")],
                       env=env, cwd=ROOT, timeout=40, check=True)
