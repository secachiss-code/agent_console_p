"""Run native GTK tests with explicit private-geometry or desktop lifecycle scope.

Use the UI virtualenv interpreter. Supply an installed Xvfb via --xvfb when
it is absent from PATH. No dependency downloads or desktop settings writes.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

ROOT = Path(__file__).resolve().parents[1]
GEOMETRY = [
    "tests/gui/front/test_b0_window.py",
    "tests/gui/front/test_b1_keys.py",
    "tests/gui/front/test_des3_visual.py",
]
DESKTOP = [
    "tests/gui/front/test_approval_alerts.py",
    "tests/gui/front/test_preferences_drafts.py",
    "tests/gui/front/test_window_badge_lifecycle.py",
]


def stop_group(process: subprocess.Popen[bytes]) -> None:
    """Release the owned session, including children left by a failed test."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        _ = process.wait(timeout=5)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    _ = process.wait(timeout=5)


@contextlib.contextmanager
def private_display(binary: str, output: Path) -> Generator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="aconsole-native-x11-") as directory:
        temp = Path(directory)
        # Xvfb's lock is authoritative if another process wins this selection.
        number = next(n for n in range(101, 1000)
                      if not Path(f"/tmp/.X11-unix/X{n}").exists()  # noqa: S108 - X11 protocol location
                      and not Path(f"/tmp/.X{n}-lock").exists())  # noqa: S108 - X server lock location
        display = str(number).encode()
        auth = temp / "Xauthority"
        _ = auth.write_bytes(struct.pack(">HH", 65535, 0) + struct.pack(">H", len(display)) + display
                         + struct.pack(">H", 18) + b"MIT-MAGIC-COOKIE-1"
                         + struct.pack(">H", 16) + secrets.token_bytes(16))
        auth.chmod(0o600)
        read_fd, write_fd = os.pipe()
        try:
            with (output / "xvfb.log").open("xb") as log:
                server = subprocess.Popen(
                    [binary, f":{number}", "-screen", "0", "1280x800x24", "-nolisten", "tcp",
                     "-auth", str(auth), "-displayfd", str(write_fd)],
                    pass_fds=(write_fd,), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
                os.close(write_fd)
                write_fd = -1
                try:
                    with selectors.DefaultSelector() as selector:
                        _ = selector.register(read_fd, selectors.EVENT_READ)
                        if not selector.select(timeout=10):
                            raise RuntimeError("Xvfb readiness timed out")
                        if os.read(read_fd, 64).strip() != display or server.poll() is not None:
                            raise RuntimeError("Xvfb failed before readiness; see xvfb.log")
                    yield {"DISPLAY": f":{number}", "XAUTHORITY": str(auth)}
                finally:
                    stop_group(server)
        finally:
            os.close(read_fd)
            if write_fd != -1:
                os.close(write_fd)


def run_tests(env: dict[str, str], tests: list[str], output: Path, timeout: float) -> int:
    command = [sys.executable, "-m", "pytest", "-o", "addopts=", "-q",
               "--junitxml=" + str(output / "pytest.xml"), *tests]
    metadata: dict[str, str | bool | int | list[str] | None] = {"command": command, "display": env.get("DISPLAY"),
                "gdk_backend": env["GDK_BACKEND"], "renderer_override": env.get("GSK_RENDERER"),
                "production_identity_changed": False}
    with (output / "pytest.log").open("xb") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result = 124
        finally:
            stop_group(process)
    metadata["returncode"] = result
    _ = (output / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return result


class Options(argparse.Namespace):
    mode: str = ""
    output: Path = Path()
    xvfb: str | None = None
    timeout: float = 120
    tests: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.tests = []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--mode", choices=("geometry", "desktop"), required=True)
    _ = parser.add_argument("--output", type=Path, required=True, help="New evidence directory; never overwritten")
    _ = parser.add_argument("--xvfb", default=shutil.which("Xvfb"))
    _ = parser.add_argument("--timeout", type=float, default=120)
    _ = parser.add_argument("tests", nargs="*", help="Optional explicit pytest paths/node IDs")
    args = parser.parse_args(namespace=Options())
    if args.mode == "geometry" and not args.xvfb:
        parser.error("geometry requires installed Xvfb or --xvfb /path/to/Xvfb")
    if args.mode == "desktop" and not os.environ.get("DISPLAY"):
        parser.error("desktop requires the real desktop DISPLAY")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, GDK_BACKEND="x11", GTK_A11Y="test", PYTHONPATH=str(ROOT / "src"))
    _ = env.pop("WAYLAND_DISPLAY", None)
    _ = env.pop("BROADWAY_DISPLAY", None)
    tests = args.tests or (GEOMETRY if args.mode == "geometry" else DESKTOP)
    if args.mode == "desktop":
        return run_tests(env, tests, output, args.timeout)
    assert args.xvfb is not None  # noqa: S101 - validated by parser above
    with private_display(args.xvfb, output) as display_env:
        env.update(display_env)
        return run_tests(env, tests, output, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
