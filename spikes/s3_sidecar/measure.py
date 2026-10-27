"""A0.5 measurements for the sidecar prototype and podman exec."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sidecar import BSU, ESU, Scanner, get_winsize, pump, spawn

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = "/home/sas/.containers/containers/agent-claude/.npm-global/bin/claude"
CODEX = "/home/sas/.containers/containers/agent-codex/.npm-global/bin/codex"
PATH_ENV = (
    "/home/sas/.containers/host-bins:"
    "/home/sas/.containers/containers/agent-claude/.npm-global/bin:"
    "/usr/bin:/bin"
)
CODEX_PATH = PATH_ENV.replace("agent-claude", "agent-codex")


def _check_scanner() -> None:
    scanner = Scanner()
    first = scanner.feed(b"A" + BSU + b"HEL")
    second = scanner.feed(b"LO" + ESU + b"Z")
    if first != b"A" or second != b"HELLOZ":
        raise SystemExit(f"scanner split failed {first!r} {second!r}")
    bel = Scanner()
    bel.feed(b"\x1b]0;title\x07")
    if bel.signals != [b"\x1b]0;title\x07"]:
        raise SystemExit(f"osc failed {bel.signals!r}")


def _child_script() -> str:
    return r"""
import os, signal, sys, time
def _int(*_a):
    sys.stdout.write("INT\n"); sys.stdout.flush()
def _term(*_a):
    sys.stdout.write("TERM\n"); sys.stdout.flush(); os._exit(0)
signal.signal(signal.SIGINT, _int)
signal.signal(signal.SIGTERM, _term)
signal.signal(signal.SIGWINCH, lambda *_: None)
sys.stdout.write("READY\n")
sys.stdout.flush()
while True:
    try:
        data = os.read(0, 16)
    except InterruptedError:
        continue
    if not data:
        break
    if data == b"\x10":
        rows, cols = os.get_terminal_size()
        sys.stdout.write(f"CTRLP {rows}x{cols}\n")
        sys.stdout.flush()
"""


def _pty_cases() -> dict[str, str]:
    child = spawn([sys.executable, "-c", _child_script()])
    pump(child, 0.3)
    child.resize(40, 120)
    time.sleep(0.05)
    child.write(b"\x03")
    pump(child, 0.2)
    child.write(b"\x10")
    pump(child, 0.3)
    rows, cols = get_winsize(child.fd)
    started = time.perf_counter()
    child.forward(signal.SIGTERM)
    pump(child, 0.5)
    term_s = time.perf_counter() - started
    text = bytes(child.output).decode(errors="replace")
    child.close()
    victim = spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    started = time.perf_counter()
    os.kill(victim.pid, signal.SIGKILL)
    os.waitpid(victim.pid, 0)
    kill_s = time.perf_counter() - started
    victim.close()
    return {
        "resize": f"{rows}x{cols}",
        "ctrl_c": "INT" if "INT" in text else text.replace("\n", " ")[:80],
        "ctrl_p": "CTRLP" if "CTRLP" in text else text.replace("\n", " ")[:80],
        "sigterm_s": f"{term_s:.4f}",
        "sigkill_s": f"{kill_s:.4f}",
        "term_seen": "TERM" if "TERM" in text else "missing",
    }


def _run(argv: list[str], env: dict[str, str] | None = None, timeout: float = 15) -> tuple[float, int, str]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout
        decoded = stdout.decode(errors="replace") if isinstance(stdout, bytes) else str(stdout)
        return time.perf_counter() - started, 124, decoded[:120]
    elapsed = time.perf_counter() - started
    text = (completed.stdout or "") + (completed.stderr or "")
    return elapsed, completed.returncode, text.strip().splitlines()[-1] if text.strip() else ""


def _podman(container: str, binary: str, extra_env: dict[str, str], path: str) -> tuple[float, int, str]:
    argv = [
        "podman",
        "exec",
        "--user",
        "sas",
        "--env",
        f"PATH={path}",
        *(item for key, value in extra_env.items() for item in ("--env", f"{key}={value}")),
        container,
        binary,
        "--version",
    ]
    return _run(argv)


def _distrobox_path() -> str:
    completed = subprocess.run(
        ["distrobox", "enter", "-d", "agent-claude"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in completed.stdout.splitlines():
        if line.startswith("--env=PATH="):
            return line.split("=", 2)[2]
    return ""


def main() -> int:
    _check_scanner()
    pty = _pty_cases()
    path = _distrobox_path()
    true_s, true_code, _ = _run(["podman", "exec", "--user", "sas", "agent-claude", "true"])
    rows = [f"podman exec true | {true_s:.4f}s | exit {true_code}"]
    for sync in ("0", "1"):
        for flicker in ("0", "1"):
            env = {
                "CLAUDE_CODE_FORCE_SYNC_OUTPUT": sync,
                "CLAUDE_CODE_NO_FLICKER": flicker,
            }
            elapsed, code, line = _podman("agent-claude", CLAUDE, env, PATH_ENV)
            rows.append(f"claude sync={sync} flicker={flicker} | {elapsed:.3f}s | {code} | {line}")
            elapsed, code, line = _podman("agent-codex", CODEX, env, CODEX_PATH)
            rows.append(f"codex sync={sync} flicker={flicker} | {elapsed:.3f}s | {code} | {line}")
    sys.stdout.write(f"pty resize {pty['resize']}\n")
    sys.stdout.write(f"pty ctrl-c {pty['ctrl_c']}\n")
    sys.stdout.write(f"pty ctrl-p {pty['ctrl_p']}\n")
    sys.stdout.write(f"pty sigterm {pty['term_seen']} {pty['sigterm_s']}s\n")
    sys.stdout.write(f"pty sigkill {pty['sigkill_s']}s\n")
    sys.stdout.write(f"distrobox PATH set {bool(path)} len {len(path)}\n")
    for row in rows:
        sys.stdout.write(row + "\n")
    resize_ok = pty["resize"] == "40x120"
    signals_ok = pty["ctrl_c"] == "INT" and "CTRLP" in pty["ctrl_p"] and pty["term_seen"] == "TERM"
    if not resize_ok or not signals_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
