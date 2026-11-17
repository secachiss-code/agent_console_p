"""Public Python declarations without executing project code or retaining bodies."""

import ast
import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileHashes:
    body: str | None
    interface: str | None


def file_hashes(root: Path, path: str, language: str | None, symbols: tuple[str, ...]) -> FileHashes:
    """Hash source bytes without executing code; an empty interface is never reusable."""
    if language is None:
        return FileHashes(None, None)
    try:
        source = root / path
        if not source.resolve().is_relative_to(root.resolve()):
            return FileHashes(None, None)
        raw = source.read_bytes()
    except OSError, ValueError:
        return FileHashes(None, None)
    body = hashlib.sha256(raw).hexdigest()
    interface = "\n".join(sorted(set(symbols))) if symbols else None
    if language == "python":
        interface = _python_interface(raw)
    return FileHashes(body, hashlib.sha256(interface.encode("utf-8")).hexdigest() if interface is not None else "")


def _public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _declarations(body: list[ast.stmt]) -> list[ast.stmt]:
    found: list[ast.stmt] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _public(node.name):
            declaration = copy.deepcopy(node)
            declaration.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
            found.append(declaration)
        elif isinstance(node, ast.ClassDef) and _public(node.name):
            declaration = copy.deepcopy(node)
            declaration.body = _declarations(node.body) or [ast.Expr(value=ast.Constant(value=Ellipsis))]
            found.append(declaration)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and _public(node.target.id):
            annotation = copy.deepcopy(node)
            annotation.value = None
            found.append(annotation)
        elif isinstance(node, ast.TypeAlias) and _public(node.name.id):
            found.append(copy.deepcopy(node))
    return found


def _python_interface(source: str | bytes) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError, ValueError:
        return None
    tree.body = _declarations(tree.body)
    return ast.unparse(ast.fix_missing_locations(tree))


def python_skeleton(source: str) -> str:
    """Invalid intermediate edits have no reliable AST and yield an empty skeleton."""
    return _python_interface(source) or ""


def python_slice(source: str, symbols: tuple[str, ...], *, context: int = 3, max_lines: int = 120) -> str:
    """Numbered, bounded source around a selected declaration, including qualified methods."""
    if context < 0 or max_lines < 1:
        message = "invalid source slice bounds"
        raise ValueError(message)
    try:
        tree = ast.parse(source)
    except SyntaxError, ValueError:
        return ""
    starts: list[int] = []
    pending: list[tuple[str, list[ast.stmt]]] = [("", tree.body)]
    while pending:
        prefix, body = pending.pop()
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = prefix + node.name
                if qualified in symbols or node.name in symbols:
                    starts.append(node.lineno)
                pending.append((qualified + ".", node.body))
    if not starts:
        return ""
    lines = source.splitlines()
    start = max(min(starts) - context - 1, 0)
    return "\n".join(f"{number + 1}: {line}" for number, line in enumerate(lines[start : start + max_lines], start))
