"""Is the running core older than the code on disk? The real core outlives its window."""

import os
from pathlib import Path

import aconsole

_SOURCE = Path(aconsole.__file__).parent
_CORE_PACKAGES = ("core", "contracts", "sidecar")
_PROC = Path("/proc")
_START_TICKS_AFTER_COMM = 19


def _started_at(pid: int, proc: Path) -> float:
    """Process start in epoch seconds, from the kernel: the mtime of a /proc entry is not the start."""
    after_comm = (proc / str(pid) / "stat").read_text(encoding="ascii").rpartition(")")[2].split()
    ticks = int(after_comm[_START_TICKS_AFTER_COMM])
    lines = (proc / "stat").read_text(encoding="ascii").splitlines()
    boot = next(int(line.split()[1]) for line in lines if line.startswith("btime "))
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def _newest_source(source: Path) -> float:
    return max(
        (file.stat().st_mtime for package in _CORE_PACKAGES for file in (source / package).rglob("*.py")),
        default=0.0,
    )


def core_is_older_than_code(pid: int, *, source: Path = _SOURCE, proc: Path = _PROC) -> bool:
    """True when core code changed after ``pid`` started. Unknown counts as fresh: never stop a core on a guess."""
    try:
        return _newest_source(source) > _started_at(pid, proc)
    except (OSError, ValueError, IndexError, StopIteration):
        return False
