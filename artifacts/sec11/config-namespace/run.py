"""Repeat the SEC-13 experiment serially; keep failed assertions and outputs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
env = dict(os.environ, PYTHONPATH=str(root / "src") + ":" + str(out))
phases = {
    "baseline-final": ["artifacts/sec11/race_probe.py"],
    "focused-final": ["-p", "namespace_plugin", "artifacts/sec11/race_probe.py",
                      "tests/core/test_git_host_exec.py", "tests/core/test_git_security_environment.py"],
    "adversarial-final": ["-p", "namespace_plugin",
                          "artifacts/sec11/config-namespace/test_snapshot_races.py"],
}
sources = [*out.glob("*.py"), root / "artifacts/sec11/race_probe.py",
           root / "src/aconsole/core/infrastructure/git/runner.py"]
hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
results = {}
for label, selection in phases.items():
    command = [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", *selection,
               "--junitxml=" + str(out / (label + ".xml"))]
    started = time.monotonic()
    with (out / (label + ".log")).open("w") as stream:
        result = subprocess.run(command, cwd=root, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, check=False, timeout=180)
    junit = ET.parse(out / (label + ".xml")).getroot().find("testsuite")
    results[label] = {"exit": result.returncode, "seconds": time.monotonic() - started,
                      "junit": junit.attrib, "command": command}
    print(label, results[label], flush=True)
assert hashes == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
(out / "results.json").write_text(json.dumps({
    "base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "python": sys.version, "namespace": os.readlink("/proc/self/ns/mnt"),
    "scope": "agent container; not a host namespace test", "hashes": hashes, "results": results,
}, indent=2) + "\n")
raise SystemExit(int(any(item["exit"] for item in results.values())))
