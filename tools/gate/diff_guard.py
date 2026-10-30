"""Reject weakened tests and edits to protected paths since a task's base commit."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict


class GuardHit(TypedDict):
    rule: str
    path: str
    detail: str


PROTECTED = (
    "tools/*",
    "tools/**",
    ".cursor/*",
    ".cursor/**",
    ".grok/*",
    ".grok/**",
    ".importlinter",
    "setup.cfg",
    "pytest.ini",
    ".basedpyright/*",
    ".basedpyright/**",
    "tox.ini",
    "ruff.toml",
    ".ruff.toml",
    "**/conftest.py",
    "*baseline*.json",
    "**/*baseline*.json",
    ".github/workflows/*",
    ".github/workflows/**",
    "docs/plan/*",
    "docs/plan/**",
)
TABLES = (
    "tool.basedpyright",
    "tool.ruff",
    "tool.importlinter",
    "tool.pytest",
    "tool.coverage",
    "tool.pytest.ini_options",
)
SKIP_PREFIXES = ("pytest.mark.skip", "pytest.mark.xfail")
SKIP_CALLS = frozenset({"pytest.skip", "pytest.xfail", "pytest.importorskip"})


def git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        message = completed.stderr.strip()
        raise SystemExit(f"git {' '.join(args)} failed: {message}")
    return completed


def show(repo: str, rev: str, path: str) -> str | None:
    completed = git(repo, "show", f"{rev}:{path}", check=False)
    if completed.returncode != 0:
        return None
    return completed.stdout


def qname(node: ast.AST) -> str:
    current = node.func if isinstance(node, ast.Call) else node
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def is_trivial_assert(node: ast.Assert) -> bool:
    test = node.test
    if isinstance(test, ast.Constant):
        return True
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Constant):
        return all(isinstance(item, ast.Constant) for item in test.comparators)
    return False


def _assert_count(fn: ast.AST) -> int:
    """Real asserts plus `pytest.raises` / `pytest.warns` checks in one function body."""
    count = 0
    for node in ast.walk(fn):
        real_assert = isinstance(node, ast.Assert) and not is_trivial_assert(node)
        raises = isinstance(node, ast.Call) and qname(node) in {"pytest.raises", "pytest.warns"}
        if real_assert or raises:
            count += 1
    return count


def _called_names(fn: ast.AST) -> set[str]:
    """Names a function calls directly: `helper(...)` and `self.helper(...)`."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in {"self", "cls"}:
            names.add(func.attr)
    return names


def _helper_asserts(helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]) -> dict[str, int]:
    """Asserts reachable from each helper through other helpers of the same scope."""
    totals: dict[str, int] = {}

    def total(name: str, seen: frozenset[str]) -> int:
        if name in totals:
            return totals[name]
        fn = helpers[name]
        count = _assert_count(fn)
        for callee in _called_names(fn):
            if callee in helpers and callee not in seen:
                count += total(callee, seen | {callee})
        totals[name] = count
        return count

    for name in helpers:
        total(name, frozenset({name}))
    return totals


def test_items(source: str) -> dict[str, dict[str, int]]:
    """Per-test assert and skip counts. Asserts in module or class helpers the test calls count too."""
    items: dict[str, dict[str, int]] = {}
    tree = ast.parse(source)
    module_helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test")
    }
    module_totals = _helper_asserts(module_helpers)

    def handle(fn: ast.FunctionDef | ast.AsyncFunctionDef, prefix: str, totals: dict[str, int]) -> None:
        asserts = _assert_count(fn) + sum(totals.get(name, 0) for name in _called_names(fn))
        skips = 0
        for decorator in fn.decorator_list:
            name = qname(decorator)
            if name.startswith(SKIP_PREFIXES) or name == "pytest.mark.skipif":
                skips += 1
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and qname(node) in SKIP_CALLS:
                skips += 1
        items[f"{prefix}{fn.name}"] = {"asserts": asserts, "skips": skips}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            handle(node, "", module_totals)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            class_helpers = {
                sub.name: sub
                for sub in node.body
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("test")
            }
            class_totals = {**module_totals, **_helper_asserts({**module_helpers, **class_helpers})}
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name.startswith("test"):
                    handle(sub, f"{node.name}::", class_totals)
    return items


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if not isinstance(value, dict):
        return out
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            out.update(_flatten(item, full))
        else:
            out[full] = item
    return out


def _table(doc: dict[str, object], dotted: str) -> dict[str, object]:
    current: object = doc
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return {}
        current = current.get(part, {})
    if isinstance(current, dict):
        return current
    return {}


def protected_table_diff(base_src: str | None, head_src: str | None) -> list[str]:
    base_doc = tomllib.loads(base_src) if base_src else {}
    head_doc = tomllib.loads(head_src) if head_src else {}
    changes: list[str] = []
    for table in TABLES:
        before = _flatten(_table(base_doc, table))
        after = _flatten(_table(head_doc, table))
        for key in sorted(set(before) | set(after)):
            old = before.get(key, "<absent>")
            new = after.get(key, "<absent>")
            if old != new:
                changes.append(f"{table}.{key}: {old!r} -> {new!r}")
    base_contracts = _contracts(base_doc)
    head_contracts = _contracts(head_doc)
    if base_contracts != head_contracts:
        changes.append("tool.importlinter.contracts array changed")
    return changes


def _contracts(doc: dict[str, object]) -> object:
    tool = doc.get("tool", {})
    if not isinstance(tool, dict):
        return []
    linter = tool.get("importlinter", {})
    if not isinstance(linter, dict):
        return []
    return linter.get("contracts", [])


_PROGRESS_PREFIX = "docs/plan/core/progress/"
_CORE_TOOL_PREFIXES = ("tools/codegraph/", "tools/csgraph/")


def is_protected(path: str) -> bool:
    """Every tool, gate, hostrun and plan file is protected, except progress records and core-owned tools.

    MAIN commits (the user or the fix track) change protected files between core tasks,
    so a task diff never contains them.
    """
    normalized = path.replace("\\", "/")
    if normalized.startswith(_PROGRESS_PREFIX):
        return False
    # Core-owned tools (ROUND7 §2): the code-graph extractors and the state generator.
    if normalized == "tools/gen_state.py" or normalized.startswith(_CORE_TOOL_PREFIXES):
        return False
    if normalized in {"AGENTS.md", "src/aconsole/core/AGENTS.md", "src/aconsole/ui/AGENTS.md"}:
        return True
    if normalized.startswith(".grok/"):
        return True
    for pattern in PROTECTED:
        regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        if re.fullmatch(regex, normalized):
            return True
    return False


def _is_test_path(path: str) -> bool:
    return path.endswith(".py") and (path.startswith("tests/") or Path(path).name.startswith("test_"))


def _source(repo: str, rev: str | None, path: str) -> str | None:
    if rev is not None:
        return show(repo, rev, path)
    target = Path(repo) / path
    return target.read_text(encoding="utf-8") if target.is_file() else None


def collect(repo: str, base: str, head: str | None) -> list[GuardHit]:
    git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    out: list[GuardHit] = []
    diff_args = ["diff", "--name-status", "-M", "--no-color", base]
    if head is not None:
        diff_args.append(head)
    names = git(repo, *diff_args).stdout
    changed: list[tuple[str, str, str]] = []
    for line in names.splitlines():
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R":
            changed.append((status, parts[1], parts[2]))
        else:
            changed.append((status, parts[1], parts[1]))
    if head is None:
        for untracked in git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines():
            changed.append(("A", untracked, untracked))
    for status, old, new in changed:
        if status == "D" and (old.startswith("tests/") or Path(old).name.startswith("test_")):
            out.append(GuardHit(rule="test-file-deleted", path=old, detail="whole test file removed"))
        if is_protected(new) or is_protected(old):
            out.append(GuardHit(rule="protected-file-changed", path=new, detail=f"status {status}"))
        if new.endswith("pyproject.toml"):
            for detail in protected_table_diff(show(repo, base, old), _source(repo, head, new)):
                out.append(GuardHit(rule="protected-pyproject-table", path=new, detail=detail))
    for old, new in [(old, new) for _status, old, new in changed if _is_test_path(old) or _is_test_path(new)]:
        base_items = test_items(show(repo, base, old) or "") if show(repo, base, old) else {}
        head_src = _source(repo, head, new)
        head_items = test_items(head_src) if head_src else {}
        for key in sorted(set(base_items) - set(head_items)):
            out.append(GuardHit(rule="test-removed", path=new, detail=f"{key} (removed or renamed)"))
        for key in sorted(set(head_items) & set(base_items)):
            if head_items[key]["asserts"] < base_items[key]["asserts"]:
                detail = f"{key}: {base_items[key]['asserts']} -> {head_items[key]['asserts']}"
                out.append(GuardHit(rule="assert-count-reduced", path=new, detail=detail))
            if head_items[key]["skips"] > base_items[key]["skips"]:
                detail = f"{key}: skips {base_items[key]['skips']} -> {head_items[key]['skips']}"
                out.append(GuardHit(rule="skip-added", path=new, detail=detail))
        for key in sorted(set(head_items) - set(base_items)):
            if head_items[key]["skips"]:
                out.append(GuardHit(rule="skip-added", path=new, detail=f"{key}: new test is skipped"))
            if head_items[key]["asserts"] == 0:
                out.append(GuardHit(rule="new-test-without-assert", path=new, detail=key))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default=None, help="commit to compare against; default is the working tree")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    hits = collect(args.repo, args.base, args.head)
    if args.json:
        payload = {"base": args.base, "head": args.head or "WORKTREE", "violations": hits}
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        for hit in hits:
            sys.stdout.write(f"{hit['rule']}: {hit['path']}: {hit['detail']}\n")
        sys.stdout.write(f"{len(hits)} guard violation(s)\n")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
