"""Print a process RSS in mebibytes. FRONT B12 reads this report.

Usage: python tools/ui_rss.py --pid PID [--limit-mib 150]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_KIB_IN_MIB = 1024


def rss_kib(pid: int) -> int:
    """Read ``VmRSS`` from ``/proc/<pid>/status``. The value is in KiB."""
    status = Path(f"/proc/{pid}/status")
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    message = f"VmRSS missing for pid {pid}"
    raise SystemExit(message)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Print the resident set size of a process.")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--limit-mib", type=float, default=None)
    args = parser.parse_args(argv)
    mebibytes = rss_kib(args.pid) / _KIB_IN_MIB
    sys.stdout.write(f"rss_mib={mebibytes:.1f}\n")
    if args.limit_mib is not None and mebibytes > args.limit_mib:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
