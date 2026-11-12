"""Sequential application-identity controls, with no changes to WM settings."""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
out = root / "artifacts/qa21"
env = dict(os.environ, GDK_BACKEND="x11", GTK_A11Y="test", PYTHONPATH=str(root / "src"))
for app_id, label in [
    ("io.github.aconsole.AConsole", "identity-original-1"),
    ("io.github.aconsole.GeometryProbe", "identity-control"),
    ("io.github.aconsole.AConsole", "identity-original-2"),
]:
    with (out / (label + ".log")).open("w") as stream:
        subprocess.run(
            [sys.executable, str(out / "app_geometry.py"), app_id, label],
            env=env,
            cwd=root,
            check=True,
            timeout=25,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
schemas = subprocess.check_output(["/usr/bin/gsettings", "list-schemas"], text=True).splitlines()
with (out / "wm-settings.txt").open("w") as stream:
    for schema in schemas:
        if "gala" in schema or "elementary.desktop.wm" in schema:
            subprocess.run(
                ["/usr/bin/gsettings", "list-recursively", schema],
                text=True,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=10,
            )
print("identity matrix complete")
