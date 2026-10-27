"""The prototype corpus still matches the production placeholder checker."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "gate"))

import no_placeholders  # noqa: E402


def test_corpus_expectations() -> None:
    corpus = ROOT / "tests" / "gate" / "corpus"
    failures: list[str] = []
    for path in sorted(corpus.glob("*.py")):
        _tree, violations = no_placeholders.check_file(path)
        got = {(item["line"], item["rule"]) for item in violations}
        expected: set[tuple[int, str]] = set()
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.search(r"# expect: (.*)$", line)
            if match is None:
                continue
            for rule in match.group(1).split():
                expected.add((index, rule))
        if path.name.startswith("neg") and violations:
            failures.append(path.name)
        elif path.name.startswith("pos") and (expected - got or got - expected):
            failures.append(f"{path.name} missing={sorted(expected - got)} extra={sorted(got - expected)}")
    assert failures == []
