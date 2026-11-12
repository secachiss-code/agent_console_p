"""Serial native watchdog validation and unchanged scale acceptance runs."""

# ruff: noqa: INP001
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/qa20"
PERF = ROOT / "artifacts/perf7"
RESULTS = {}


def run(label: str, args: list[str]) -> None:
    if (PERF / f"{label}.xml").exists():
        message = f"refusing to replace evidence: {label}"
        raise FileExistsError(message)
    command = [sys.executable, str(PERF / "runner.txt"), "--desktop", "--label", label, *args]
    result = subprocess.run(command, cwd=ROOT, timeout=420, check=False)  # noqa: S603 -- fixed native validation commands
    RESULTS[label] = result.returncode
    (OUTPUT / "exit-codes.json").write_text(json.dumps(RESULTS, indent=2) + "\n")


if __name__ == "__main__":
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), GDK_BACKEND="x11", GTK_A11Y="test")
    focused = subprocess.run(  # noqa: S603 -- fixed regression test
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "tests/gui/front/test_watchdog_cleanup.py",
         "--junitxml=" + str(OUTPUT / "focused.xml")], cwd=ROOT, env=env, timeout=30, check=False,
    )
    RESULTS["focused"] = focused.returncode
    if focused.returncode:
        raise SystemExit(focused.returncode)
    run("qa20-sources-full", ["--mode", "full", "--sources", "--lifecycle"])
    run("qa20-full", ["--mode", "full", "--lifecycle"])
    run("qa20-isolated", ["--mode", "isolated", "--lifecycle"])
    sys.stdout.write(json.dumps(RESULTS, indent=2) + "\n")
