"""Reject JavaScript sources. TypeScript is allowed."""

from __future__ import annotations

import sys
from pathlib import Path

JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs"}
SKIP_PARTS = {
    ".aconsole-dev",
    ".git",
    ".venv",
    ".venv-core",
    ".venv-daemon",
    ".venv-ui",
    "corpus",
    "fixtures",
    "gate_prototype",
    "node_modules",
}


def scan(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in paths:
        if path.is_file() and path.suffix in JS_SUFFIXES:
            hits.append(str(path))
            continue
        if not path.is_dir():
            continue
        for item in sorted(path.rglob("*")):
            if SKIP_PARTS.intersection(item.parts):
                continue
            if item.is_file() and item.suffix in JS_SUFFIXES:
                hits.append(str(item))
    return hits


def main(argv: list[str]) -> int:
    roots = [Path(arg) for arg in argv] or [Path()]
    hits = scan(roots)
    for hit in hits:
        sys.stdout.write(f"javascript source: {hit}\n")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
