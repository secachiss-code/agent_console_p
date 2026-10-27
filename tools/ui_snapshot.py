"""CLI for hostrun ui-snapshot. The spike writes one PNG and exits."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aconsole.ui.spike import run_spike


class _SnapshotArguments(argparse.Namespace):
    scenario: str = "demo"
    view: str = "window"
    theme: str = "light"
    size: str = "1280x800"
    out: Path = Path("docs/ui/B0/window.png")


def main(argv: list[str]) -> int:
    if argv == ["--spike"]:
        destination = Path("docs/ui/spike.png")
        destination.parent.mkdir(parents=True, exist_ok=True)
        run_spike(snapshot=destination, autoquit=True)
        if not destination.is_file() or destination.stat().st_size == 0:
            sys.stdout.write("snapshot missing\n")
            return 1
        sys.stdout.write(f"wrote {destination}\n")
        return 0
    from aconsole.__main__ import run_ui  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--scenario", required=True, choices=("demo", "empty", "stress", "errors"))
    _ = parser.add_argument("--view", required=True, choices=("window",))
    _ = parser.add_argument("--theme", required=True, choices=("light", "dark"))
    _ = parser.add_argument("--size", required=True)
    _ = parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv, namespace=_SnapshotArguments())
    result = run_ui([
        "--fake", args.scenario, "--snapshot-view", args.view, "--theme", args.theme,
        "--size", args.size, "--out", str(args.out),
    ])
    if result or not args.out.is_file() or args.out.stat().st_size == 0:
        _ = sys.stderr.write("snapshot missing or application failed\n")
        return result or 1
    _ = sys.stdout.write(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
