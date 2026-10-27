"""hostrun sources must remain parseable by the host service's Python 3.12."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOSTRUN_SOURCES = ROOT / "tools" / "hostrun"


def test_hostrun_sources_use_python_312_syntax() -> None:
    for path in sorted(HOSTRUN_SOURCES.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path), feature_version=(3, 12))
