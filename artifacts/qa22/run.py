"""Serial native controls and independently traced original failure scenarios.

The geometry series changes Gala's saved geometry. See REPORT.md before use.
Use --trace-only with a fresh label for diagnostics without geometry controls.
"""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
output = root / "artifacts/qa22"
env = dict(os.environ, PYTHONPATH=str(root / "src"), GDK_BACKEND="x11", GTK_A11Y="test")
cases = ([("trace_failures.py", sys.argv[2])] if "--trace-only" in sys.argv else
         [("probe.py", "fixed-restore"), ("probe.py", "normal-restored"),
          ("trace_failures.py", "desktop-full-trace")] if "--restore" in sys.argv else
         [("probe.py", "fixed"), ("probe.py", "control"),
          ("probe.py", "normal-repeat"), ("trace_failures.py", "desktop-trace")])
for script, label in cases:
    with (output / (label + ".log")).open("x") as stream:
        result = subprocess.run([sys.executable, str(output / script), label], env=env, cwd=root,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=45, check=False)
    print(label, result.returncode, flush=True)
    if script == "probe.py" and result.returncode:
        raise SystemExit(result.returncode)
raise SystemExit(result.returncode)
