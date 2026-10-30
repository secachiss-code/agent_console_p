"""Run one command on a private, authenticated X server (B1 request).

`isolated_x.py -- <argv>` starts a rootful Xwayland on a free display with its own
MIT-MAGIC-COOKIE, runs <argv> with DISPLAY, XAUTHORITY and GDK_BACKEND=x11 pointing
at it, then stops the server. XTest events reach only that server, never the user's
session. Xwayland itself needs the user's WAYLAND_DISPLAY and XDG_RUNTIME_DIR.
"""

from __future__ import annotations

import os
import secrets
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

XWAYLAND = "/usr/bin/Xwayland"
_DISPLAYS = range(90, 140)
_READY_S = 15.0
_GEOMETRY = "1280x800"
_FAMILY_WILD = 0xFFFF


_X11_DIR = Path("/tmp/.X11-unix")  # noqa: S108 -- the X server socket directory
_LOCK_DIR = Path("/tmp")  # noqa: S108 -- X lock files live here


def free_display(x11_dir: Path = _X11_DIR, lock_dir: Path = _LOCK_DIR) -> int:
    for number in _DISPLAYS:
        if not (x11_dir / f"X{number}").exists() and not (lock_dir / f".X{number}-lock").exists():
            return number
    message = "no free X display in 90..139"
    raise RuntimeError(message)


def xauthority(number: int, cookie: bytes) -> bytes:
    """One FamilyWild entry: any host, this display, MIT-MAGIC-COOKIE-1."""

    def field(value: bytes) -> bytes:
        return struct.pack(">H", len(value)) + value

    return (
        struct.pack(">H", _FAMILY_WILD)
        + field(b"")
        + field(str(number).encode())
        + field(b"MIT-MAGIC-COOKIE-1")
        + field(cookie)
    )


def child_env(base: dict[str, str], number: int, auth: Path) -> dict[str, str]:
    env = {key: value for key, value in base.items() if key != "WAYLAND_DISPLAY"}
    env["DISPLAY"] = f":{number}"
    env["XAUTHORITY"] = str(auth)
    env["GDK_BACKEND"] = "x11"
    return env


def _wait_ready(read_fd: int, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + _READY_S
    while time.monotonic() < deadline:
        ready, _, _ = select.select([read_fd], [], [], 0.1)
        if ready and os.read(read_fd, 32).strip():
            return
        if server.poll() is not None:
            message = f"Xwayland exited with {server.returncode} before it was ready"
            raise RuntimeError(message)
    message = "Xwayland was not ready in 15 s"
    raise RuntimeError(message)


def run(command: list[str], *, xwayland: str = XWAYLAND, env: dict[str, str] | None = None) -> int:
    base = dict(os.environ if env is None else env)
    if not base.get("WAYLAND_DISPLAY") or not base.get("XDG_RUNTIME_DIR"):
        sys.stderr.write("isolated_x: WAYLAND_DISPLAY and XDG_RUNTIME_DIR are required for Xwayland\n")
        return 2
    number = free_display()
    workdir = Path(tempfile.mkdtemp(prefix="aconsole-x-"))
    auth = workdir / "Xauthority"
    auth.write_bytes(xauthority(number, secrets.token_bytes(16)))
    auth.chmod(0o600)
    read_fd, write_fd = os.pipe()
    server = subprocess.Popen(
        [
            xwayland,
            f":{number}",
            "-geometry",
            _GEOMETRY,
            "-auth",
            str(auth),
            "-nolisten",
            "tcp",
            "-displayfd",
            str(write_fd),
        ],
        env=base,
        pass_fds=(write_fd,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    os.close(write_fd)
    try:
        _wait_ready(read_fd, server)
        return subprocess.run(command, env=child_env(base, number, auth), check=False).returncode
    except RuntimeError as exc:
        sys.stderr.write(f"isolated_x: {exc}\n")
        return 2
    finally:
        os.close(read_fd)
        server.terminate()
        try:
            _ = server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            _ = server.wait()
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "--":
        sys.stderr.write("usage: isolated_x.py -- COMMAND [ARG...]\n")
        return 2
    return run(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
