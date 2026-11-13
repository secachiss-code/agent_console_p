"""Inspect local coverage without CLI report auto-combine."""
# ruff: noqa: INP001, T201 -- standalone bounded evidence command
import inspect
import json
from pathlib import Path

import coverage
import coverage.cmdline

out = Path(__file__).resolve().parent
cov = coverage.Coverage(data_file=str(out / ".coverage"))
cov.load()
with (out / "api-report.txt").open("x") as stream:
    total = cov.report(file=stream, precision=8)
(out / "coverage-runtime.json").write_text(json.dumps({
    "version": coverage.__version__, "module": coverage.__file__,
    "total": total, "fail_under": cov.get_option("report:fail_under"),
    "source": cov.get_option("run:source"), "branch": cov.get_option("run:branch"),
    "command_line_source": inspect.getsource(coverage.cmdline.CoverageScript.command_line),
}, indent=2) + "\n")
print(total)
