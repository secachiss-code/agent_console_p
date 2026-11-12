"""Serial native lifecycle regression, source diagnostics and scale verification."""

# ruff: noqa: INP001, S101
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/mem2"
PERF = ROOT / "artifacts/perf7"
NEW_TESTS = ["tests/gui/front/test_bootstrap_lifecycle.py", "tests/gui/front/test_window_badge_lifecycle.py"]
RESULTS: dict[str, int] = {}


def run(label: str, command: list[str], cwd: Path, env: dict[str, str], timeout: int = 420) -> int:
    with (OUTPUT / f"{label}.log").open("w") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout, check=False,  # noqa: S603 -- fixed validation argv
                                stdout=stream, stderr=subprocess.STDOUT)
    RESULTS[label] = result.returncode
    (OUTPUT / "exit-codes.json").write_text(json.dumps(RESULTS, indent=2) + "\n")
    _ = sys.stdout.write(f"{label}: {result.returncode}\n")
    sys.stdout.flush()
    return result.returncode


def main() -> None:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), GDK_BACKEND="x11", GTK_A11Y="test")
    pytest = [sys.executable, "-m", "pytest", "-o", "addopts=", "-q"]
    with tempfile.TemporaryDirectory(prefix="mem2-red-") as directory:
        base = Path(directory)
        archive = base / "base.tar"
        subprocess.run(  # noqa: S603 -- fixed local Git object and paths
            ["/usr/bin/git", "archive", "--output=" + str(archive), "8e1aa930", "src", "tests", "pyproject.toml"],
            cwd=ROOT, check=True,
        )
        with tarfile.open(archive) as held:
            held.extractall(base, filter="data")
        for path in NEW_TESTS:
            shutil.copyfile(ROOT / path, base / path)
        red_env = dict(env, PYTHONPATH=str(base / "src"))
        assert run("native-red", [*pytest, *NEW_TESTS, "--junitxml=" + str(OUTPUT / "native-red.xml")],
                   base, red_env, 45) == 1
    assert run("native-green", [*pytest, *NEW_TESTS, "tests/gui/test_bootstrap.py", "tests/gui/front/test_b5a_page.py",
                               "--junitxml=" + str(OUTPUT / "native-green.xml")], ROOT, env, 45) == 0
    for label, args in (
        ("mem2-sources-full", ["--mode", "full", "--sources", "--lifecycle"]),
        ("mem2-full", ["--mode", "full", "--lifecycle"]),
        ("mem2-isolated", ["--mode", "isolated", "--lifecycle"]),
    ):
        if (PERF / f"{label}.xml").exists():
            message = f"refusing to replace evidence: {label}"
            raise FileExistsError(message)
        _ = run(label, [sys.executable, str(PERF / "runner.txt"), "--desktop", "--label", label, *args],
                ROOT, env)
    assert run("screenshots", [sys.executable, str(OUTPUT / "screenshots.txt")], ROOT, env, 180) == 0


if __name__ == "__main__":
    main()
