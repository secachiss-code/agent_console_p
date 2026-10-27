"""Placeholder bodies, banned typing aliases, and comment bypasses."""

from __future__ import annotations

import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import TypedDict


class Violation(TypedDict):
    file: str
    line: int
    col: int
    rule: str
    message: str


BANNED_QUALIFIED = frozenset(
    {
        "typing.Any",
        "typing.cast",
        "typing_extensions.Any",
        "typing_extensions.cast",
    }
)
TYPING_MODULES = frozenset({"typing", "typing_extensions"})
PROTOCOL_QUALIFIED = frozenset({"typing.Protocol", "typing_extensions.Protocol"})
OVERLOAD_QUALIFIED = frozenset({"typing.overload", "typing_extensions.overload"})
ABSTRACT_PREFIX = "abc.abstract"
RE_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\b")
RE_PYRIGHT = re.compile(r"#\s*(?:based)?pyright:\s*(.*)$")
RE_PYRIGHT_IGNORE_REASONED = re.compile(r"ignore\[\w+\]\s*--\s*\S")
RE_NOQA = re.compile(r"#\s*noqa\b", re.IGNORECASE)
RE_NOQA_CODES = re.compile(r"#\s*noqa:\s*([A-Za-z]+\d+(?:\s*,\s*[A-Za-z]+\d+)*)(?![\w,])", re.IGNORECASE)
RE_RUFF_NOQA = re.compile(r"#\s*ruff:\s*noqa\b", re.IGNORECASE)


def _violation(path: Path, line: int, col: int, rule: str, message: str) -> Violation:
    return Violation(file=str(path), line=line, col=col, rule=rule, message=message)


def _at(path: Path, node: ast.AST, rule: str, message: str) -> Violation:
    return _violation(path, node.lineno, node.col_offset, rule, message)


def build_aliases(tree: ast.AST, path: Path, out: list[Violation]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            for alias in node.names:
                if alias.name == "*":
                    if node.module in TYPING_MODULES:
                        out.append(
                            _at(
                                path,
                                node,
                                "banned-star-import",
                                f"star import from {node.module} hides cast/Any",
                            )
                        )
                    continue
                qualified = f"{node.module}.{alias.name}"
                aliases[alias.asname or alias.name] = qualified
                if qualified in BANNED_QUALIFIED:
                    out.append(
                        _at(
                            path,
                            node,
                            "banned-typing-import",
                            f"import of {qualified} (bound as {alias.asname or alias.name})",
                        )
                    )
    return aliases


def qualify(expr: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id)
    if isinstance(expr, ast.Attribute):
        base = qualify(expr.value, aliases)
        if base is None:
            return None
        return f"{base}.{expr.attr}"
    if isinstance(expr, ast.Call):
        return qualify(expr.func, aliases)
    if isinstance(expr, ast.Subscript):
        return qualify(expr.value, aliases)
    return None


def is_stub_body(body: list[ast.stmt]) -> bool:
    statements = list(body)
    if statements and isinstance(statements[0], ast.Expr):
        value = statements[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            statements = statements[1:]
    if not statements:
        return True
    for statement in statements:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            if statement.value.value is Ellipsis:
                continue
        if isinstance(statement, ast.Raise) and statement.exc is not None:
            exc = statement.exc.func if isinstance(statement.exc, ast.Call) else statement.exc
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                continue
        return False
    return True


def _is_protocol(
    cls: ast.ClassDef,
    aliases: dict[str, str],
    local: dict[str, ast.ClassDef],
    seen: tuple[str, ...],
) -> bool:
    for base in cls.bases:
        qualified = qualify(base, aliases)
        if qualified in PROTOCOL_QUALIFIED:
            return True
        name = base.id if isinstance(base, ast.Name) else None
        if isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name):
            name = base.value.id
        if name is not None and name in local and name not in seen:
            if _is_protocol(local[name], aliases, local, (*seen, name)):
                return True
    return False


def _exempt(node: ast.FunctionDef | ast.AsyncFunctionDef, aliases: dict[str, str]) -> bool:
    for decorator in node.decorator_list:
        qualified = qualify(decorator, aliases) or ""
        if qualified.startswith(ABSTRACT_PREFIX) or qualified in OVERLOAD_QUALIFIED:
            return True
    return False


def _walk(
    node: ast.AST,
    path: Path,
    aliases: dict[str, str],
    local: dict[str, ast.ClassDef],
    in_protocol: bool,
    out: list[Violation],
) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            _walk(child, path, aliases, local, in_protocol or _is_protocol(child, aliases, local, ()), out)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_stub_body(child.body) and not (in_protocol or _exempt(child, aliases)):
                out.append(
                    _at(
                        path,
                        child,
                        "placeholder-body",
                        f"function {child.name} body is only ...|pass|docstring|NotImplementedError",
                    )
                )
            _walk(child, path, aliases, local, in_protocol, out)
        else:
            _walk(child, path, aliases, local, in_protocol, out)


def check_file(path: Path) -> tuple[ast.AST | None, list[Violation]]:
    out: list[Violation] = []
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return None, [_violation(path, exc.lineno or 0, exc.offset or 0, "parse-error", str(exc))]
    aliases = build_aliases(tree, path, out)
    local = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    _walk(tree, path, aliases, local, False, out)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Attribute, ast.Name)) and isinstance(node.ctx, ast.Load):
            qualified = qualify(node, aliases)
            if qualified in BANNED_QUALIFIED:
                out.append(_at(path, node, "banned-typing-use", f"use of {qualified}"))
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        text = token.string
        line, col = token.start
        if RE_TYPE_IGNORE.search(text):
            out.append(_violation(path, line, col, "type-ignore", "# type: ignore comment"))
        match = RE_PYRIGHT.search(text)
        if match is not None:
            directive = match.group(1).strip()
            if directive.startswith("ignore"):
                if RE_PYRIGHT_IGNORE_REASONED.match(directive) is None:
                    message = "pyright/basedpyright ignore needs [rule] and -- reason"
                    out.append(_violation(path, line, col, "pyright-ignore", message))
            elif not directive.startswith("strict"):
                out.append(_violation(path, line, col, "pyright-file-mode", f"# pyright: {directive}"))
        if RE_RUFF_NOQA.search(text):
            out.append(_violation(path, line, col, "noqa", "# ruff: noqa file-level suppression"))
        if RE_NOQA.search(text):
            codes = RE_NOQA_CODES.search(text)
            if codes is None:
                out.append(_violation(path, line, col, "noqa", "bare # noqa; use # noqa: <CODE>"))
            elif "TID251" in {code.strip().upper() for code in codes.group(1).split(",")}:
                out.append(_violation(path, line, col, "noqa", "# noqa: TID251 is forbidden"))
    return tree, out


def iter_py(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(path.rglob("*.py")))
        else:
            found.append(path)
    return found


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    paths = [arg for arg in argv if arg != "--json"]
    violations: list[Violation] = []
    parse_error = False
    for path in iter_py(paths):
        _tree, found = check_file(path)
        for item in found:
            if item["rule"] == "parse-error":
                parse_error = True
        violations.extend(found)
    if as_json:
        sys.stdout.write(json.dumps({"violations": violations, "count": len(violations)}) + "\n")
    else:
        for item in violations:
            sys.stdout.write(f"{item['file']}:{item['line']}:{item['col']}: {item['rule']} {item['message']}\n")
        sys.stdout.write(f"{len(violations)} violation(s)\n")
    if parse_error:
        return 2
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
