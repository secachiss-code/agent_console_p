"""Run an approved editor extension in an isolated VSCodium profile."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Literal

import msgspec

from aconsole.core.infrastructure.analyzers.host_install import HostInstallError

PYTHON = "/usr/bin/python3"
_BRIDGE_MANIFEST = {
    "name": "diagnostics-bridge",
    "publisher": "aconsole",
    "version": "0.0.1",
    "engines": {"vscode": "^1.91.0"},
    "main": "./bridge.cts",
    "activationEvents": ["*"],
}
_SETTINGS: dict[str, object] = {
    "extensions.autoCheckUpdates": False,
    "extensions.autoUpdate": False,
    "extensions.ignoreRecommendations": True,
    "telemetry.telemetryLevel": "off",
    "update.mode": "none",
    "workbench.startupEditor": "none",
    "ruff.importStrategy": "useBundled",
    "pylint.importStrategy": "useBundled",
    "python.defaultInterpreterPath": PYTHON,
    "pylint.interpreter": [PYTHON],
    "python.terminal.activateEnvironment": False,
}


class ReportFinding(msgspec.Struct, frozen=True):
    path: str
    line: int
    column: int
    end_line: int
    end_column: int
    severity: str
    code: str | None
    message: str


class BridgeReport(msgspec.Struct, frozen=True):
    diagnostics: tuple[ReportFinding, ...] = ()
    observed: tuple[str, ...] = ()
    truncated: bool = False
    error: str | None = None
    failure: Literal["busy", "unavailable", "timeout"] | None = None


def _write_json(path: Path, body: object) -> None:
    _ = path.write_text(json.dumps(body), encoding="utf-8")


def _stage_files(workspace: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix not in {".py", ".pyi"}:
            raise ValueError
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_text(content, encoding="utf-8")


def run(
    binary: Path,
    extension: str,
    sources: tuple[str, ...],
    packages: dict[str, Path],
    files: dict[str, str],
) -> BridgeReport:
    if extension == "ms-python.pylint" and not os.access(PYTHON, os.X_OK):
        message = f"Pylint requires an executable interpreter at {PYTHON}"
        raise HostInstallError(message)
    xvfb = shutil.which("xvfb-run")
    if xvfb is None:
        message = "Managed Code-OSS requires xvfb-run"
        raise HostInstallError(message)
    with tempfile.TemporaryDirectory(prefix="aconsole-analyze-") as directory:
        scratch = Path(directory)
        profile, extensions, workspace = (scratch / name for name in ("profile", "extensions", "workspace"))
        extensions.mkdir()
        (profile / "User").mkdir(parents=True)
        workspace.mkdir()
        _write_json(profile / "User" / "settings.json", _SETTINGS)
        _stage_files(workspace, files)
        for identity, package in packages.items():
            _ = shutil.copytree(package, extensions / identity)
        bridge = extensions / "aconsole.diagnostics-bridge"
        bridge.mkdir()
        _ = shutil.copy2(Path(__file__).with_name("bridge.cts"), bridge)
        _write_json(bridge / "package.json", _BRIDGE_MANIFEST)
        job, output = scratch / "job.json", scratch / "report.json"
        _write_json(job, {"extension": extension, "sources": sources, "files": tuple(files), "output": str(output)})
        return _collect(binary, xvfb, scratch, job, output)


def _collect(binary: Path, xvfb: str, scratch: Path, job: Path, output: Path) -> BridgeReport:
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LD_LIBRARY_PATH") if key in os.environ}
    env.update(
        ACONSOLE_ANALYZER_JOB=str(job), HOME=str(scratch), TMPDIR=str(scratch),
        XDG_CONFIG_HOME=str(scratch / "config"),
    )
    command = (
        xvfb,
        "-a",
        str(binary),
        "--new-window",
        "--disable-gpu",
        "--disable-telemetry",
        "--disable-updates",
        "--disable-workspace-trust",
        "--user-data-dir",
        str(scratch / "profile"),
        "--extensions-dir",
        str(scratch / "extensions"),
        str(scratch / "workspace"),
    )
    with (
        (scratch / "host.log").open("w+b") as log,
        subprocess.Popen(  # noqa: S603 -- pinned host and private paths
            command,
            cwd=scratch,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        ) as process,
    ):
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if output.is_file():
                    return msgspec.json.decode(output.read_bytes(), type=BridgeReport)
                if process.poll() is not None:
                    _ = log.seek(0, os.SEEK_END)
                    _ = log.seek(max(0, log.tell() - 4096))
                    detail = log.read(4096).decode(errors="replace")[-1000:]
                    return BridgeReport(error=f"Extension Host exited ({process.returncode}): {detail}")
                time.sleep(0.15)
            return BridgeReport(error="Timed out waiting for Extension Host diagnostics", failure="timeout")
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                _ = process.wait(timeout=2)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
