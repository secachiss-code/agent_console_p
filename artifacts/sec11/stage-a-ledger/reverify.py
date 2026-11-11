"""Run existing evidence verifiers with the existing core Python environment."""

import json
import subprocess
import sys

from collect import ROOT, SOURCES

runs = {}
for key, directory in SOURCES.items():
    if key == "SEC-15":
        continue  # Historical source drift, explicitly retained in ledger.json.
    script = f"artifacts/sec11/{directory}/verify.py"
    result = subprocess.run(
        [sys.executable, script], cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    runs[key] = {
        "command": [sys.executable, script], "exit": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    }
print(json.dumps(runs, indent=2))
sys.exit(int(any(row["exit"] for row in runs.values())))
