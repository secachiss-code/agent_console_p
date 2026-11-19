"""Print a process RSS in mebibytes. FRONT B12 reads this report.

Usage: python tools/ui_rss.py --pid PID [--limit-mib 150 | --core-budget-report PATH]
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

_KIB_IN_MIB = 1024
_CORE_BASELINE_MIB = 175.81
_CORE_ALLOWANCE_MIB = 8.0
_CORE_LIMIT_MIB = _CORE_BASELINE_MIB + _CORE_ALLOWANCE_MIB


class _Arguments(argparse.Namespace):
    pid: int = 0
    limit_mib: float | None = None
    core_budget_report: Path | None = None


def rss_kib(pid: int) -> int:
    """Read ``VmRSS`` from ``/proc/<pid>/status``. The value is in KiB."""
    status = Path(f"/proc/{pid}/status")
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    message = f"VmRSS missing for pid {pid}"
    raise SystemExit(message)


def _write_core_report(path: Path, pid: int, kib: int) -> None:
    mebibytes = kib / _KIB_IN_MIB
    command = shlex.join(
        (".venv-core/bin/python", "tools/ui_rss.py", "--pid", str(pid), "--core-budget-report", str(path))
    )
    verdict = "PASS" if mebibytes <= _CORE_LIMIT_MIB else "FAIL"
    reading = f"RSS ядра — **{mebibytes:.2f} МиБ** ({kib} КиБ). База TD-HDG-MEM-1 — {_CORE_BASELINE_MIB:.2f} МиБ."
    budget = f"Допуск — {_CORE_ALLOWANCE_MIB:.0f} МиБ, предел — {_CORE_LIMIT_MIB:.2f} МиБ. Результат — **{verdict}**."
    _ = path.write_text(
        f"# MEM-1 — бюджет RSS ядра AConsole 0.1.4\n\n{reading} {budget}\n\n```sh\n{command}\n```\n",
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Print the resident set size of a process.")
    _ = parser.add_argument("--pid", type=int, required=True)
    limits = parser.add_mutually_exclusive_group()
    _ = limits.add_argument("--limit-mib", type=float)
    _ = limits.add_argument(
        "--core-budget-report", type=Path, help="Apply the AConsole 0.1.4 core budget and write a report."
    )
    args = parser.parse_args(argv, namespace=_Arguments())
    kib = rss_kib(args.pid)
    mebibytes = kib / _KIB_IN_MIB
    _ = sys.stdout.write(f"rss_mib={mebibytes:.1f}\n")
    limit = args.limit_mib
    if args.core_budget_report is not None:
        _write_core_report(args.core_budget_report, args.pid, kib)
        limit = _CORE_LIMIT_MIB
    return int(limit is not None and mebibytes > limit)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
