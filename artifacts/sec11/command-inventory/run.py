"""Static evidence only: expressions are not an executable Git allowlist."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
WRAPPERS = {
    "git/checkpoints.py": {"self._stdout", "self._z"},
    "git/diff.py": {"self._stdout", "self._git_stdout"},
    "git/worktrees.py": {"self._run"},
    "tabs.py": {"self._ask"},
    "mcp/tools.py": {"_git"},
}
PROCESS_CALLS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
    "os.system", "os.popen", "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "os.posix_spawn", "os.posix_spawnp",
}


class Scan(ast.NodeVisitor):
    def __init__(self, path, tree):
        self.path = path
        self.scope = []
        self.rows = []
        self.constants = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.constants[node.target.id] = node.value
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.constants[target.id] = node.value
        self.relative = str(path.relative_to(ROOT / "src/aconsole/core/infrastructure")) if path.is_relative_to(
            ROOT / "src/aconsole/core/infrastructure") else None

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def strings(self, node, seen=()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [part for element in node.elts for part in self.strings(element, seen)]
        if isinstance(node, ast.Name) and node.id in self.constants and node.id not in seen:
            return self.strings(self.constants[node.id], (*seen, node.id))
        return [{"expression": ast.unparse(node)}]

    def visit_Call(self, node):
        target = ast.unparse(node.func)
        kind = None
        if target == "GitRunner":
            kind = "runner_construction"
        elif target in PROCESS_CALLS:
            kind = "process_creation"
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {"spawn_async", "spawn_sync"}:
            kind = "library_process_candidate"
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {"read", "write", "invoke"}:
            receiver = ast.unparse(node.func.value)
            if receiver in {"self._git", "self._runner", "_GIT_RUNNER"} or receiver.startswith("GitRunner("):
                kind = "runner_candidate"
            elif self.relative == "git/runner.py" and receiver == "self":
                kind = "runner_internal"
        elif target in WRAPPERS.get(self.relative, set()):
            kind = "reviewed_wrapper_call"
        elif self.relative == "git/operations.py" and target in {"self._hook", "self._run_hook"}:
            kind = "direct_hook_discovery_or_execution"
        elif self.relative == "git/runner.py" and target in {"self.invoke", "self._spawn", "self._execute"}:
            kind = "runner_internal"
        if kind:
            self.rows.append({
                "path": str(self.path.relative_to(ROOT)), "line": node.lineno,
                "scope": ".".join(self.scope), "kind": kind, "target": target,
                "arguments": [ast.unparse(a) for a in node.args],
                "keywords": {k.arg or "**": ast.unparse(k.value) for k in node.keywords},
                "module_constant_expansion": [self.strings(a) for a in node.args],
            })
        self.generic_visit(node)


def collect():
    sources = sorted((ROOT / "src").rglob("*.py"))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    rows = []
    imports = []
    for path in sources:
        tree = ast.parse(path.read_bytes())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "aconsole.core.infrastructure.git.runner":
                imports.append({"path": str(path.relative_to(ROOT)), "line": node.lineno,
                                "names": [{"name": a.name, "alias": a.asname} for a in node.names]})
        scan = Scan(path, tree)
        scan.visit(tree)
        rows.extend(scan.rows)
    return {"source_hashes": hashes, "runner_imports": imports,
            "calls": sorted(rows, key=lambda r: (r["path"], r["line"]))}


def main():
    collected = collect()
    collected["base_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    collected["scanner_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    collected["scope"] = "AST expressions plus manually selected wrappers; not runtime resolution or allowlist"
    (HERE / "inventory.json").write_text(json.dumps(collected, indent=2) + "\n")
    counts = {kind: sum(row["kind"] == kind for row in collected["calls"])
              for kind in sorted({row["kind"] for row in collected["calls"]})}
    print(json.dumps({"files": len(collected["source_hashes"]), "imports": len(collected["runner_imports"]),
                      "counts": counts}, indent=2))


if __name__ == "__main__":
    main()
