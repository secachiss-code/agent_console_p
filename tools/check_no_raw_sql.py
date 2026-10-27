"""Reject raw SQL outside the persistence engine module."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PATTERNS = (
    # `text(` imported bare, or `sa.text(` / `sqlalchemy.text(`; not `st.text(` from hypothesis.
    re.compile(r"(?<![\w.])text\s*\("),
    re.compile(r"\b(?:sa|sqlalchemy)\.text\s*\("),
    re.compile(r"\bexec_driver_sql\s*\("),
    re.compile(r"""\.execute\(\s*['"]"""),
)
# The engine owns PRAGMA and BEGIN IMMEDIATE; its own test reads them back.
ALLOWED = (
    "src/aconsole/core/infrastructure/persistence/engine.py",
    "tests/core/test_persistence_a.py",
)
SKIP_PARTS = {".venv", ".venv-core", ".venv-ui", ".venv-daemon", ".aconsole-dev", "fixtures", "corpus"}


def scan(paths: list[Path]) -> list[str]:
    hits: list[str] = []
    for path in _files(paths):
        relative = path.as_posix()
        if any(relative.endswith(allowed) for allowed in ALLOWED):
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append(f"{path}:{index}: raw SQL")
    return hits


def _files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            for item in sorted(path.rglob("*.py")):
                if SKIP_PARTS.intersection(item.parts):
                    continue
                found.append(item)
        elif path.suffix == ".py":
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    roots = [Path(arg) for arg in argv] or [Path("src"), Path("tests")]
    hits = scan(roots)
    for hit in hits:
        sys.stdout.write(hit + "\n")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
