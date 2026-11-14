# ruff: noqa: ANN201, EM101, INP001, S603, S607, SIM105, SLF001, T201, TRY003
# Standalone evidence runner; dynamic protocol fixtures and bounded subprocesses are intentional.
"""One bounded final core run after the observed QA6 regression fix."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa6"


def manifest():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("src", "tests", "contracts") for path in sorted((ROOT / folder).rglob("*"))
            if path.is_file() and path.suffix in {".py", ".css", ".json", ".toml"}}


TAG = "core-verified"
RECORD = OUT / (TAG + "-result.json")
if RECORD.exists():
    raise SystemExit("Refusing to overwrite final run evidence")
before = manifest()
command = [sys.executable, "-m", "pytest", "tests/core", "tests/contracts", "tests/gate", "tests/hostrun",
           "tests/smoke", "tests/test_package.py", "tests/test_packaging.py", "tests/test_contrast.py",
           "tests/test_build_guards.py", "tests/test_socket_paths.py", "-m", "not performance",
           "-p", "no:cacheprovider", "--basetemp=/tmp/aconsole-qa6-core-final-20261114",
           "--junitxml=artifacts/qa6/" + TAG + ".xml", "-q"]
data = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "command": command, "source_before": before, "start_ns": time.time_ns(),
        "python": sys.version, "gil_enabled": sys._is_gil_enabled(), "state": "starting",
        "runner_pid": os.getpid(), "runner_pgid": os.getpgrp()}
RECORD.write_text(json.dumps(data, indent=2) + "\n")
with (OUT / (TAG + ".log")).open("xb") as log:
    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    data.update(state="running", pytest_pid=process.pid, pytest_pgid=os.getpgid(process.pid))
    RECORD.write_text(json.dumps(data, indent=2) + "\n")
    try:
        status = process.wait(timeout=900)
    except subprocess.TimeoutExpired:
        status = 124
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
data.update(exit=status, end_ns=time.time_ns(), state="pytest_finished")
RECORD.write_text(json.dumps(data, indent=2) + "\n")
data.update(source_after=manifest(), state="complete")
data["source_unchanged"] = data["source_before"] == data["source_after"]
RECORD.write_text(json.dumps(data, indent=2) + "\n")
print(json.dumps({key: data[key] for key in ("exit", "source_unchanged", "gil_enabled")}))
raise SystemExit(status)
