"""Read-only source anchors for SEC-30; no product imports or Git mutations."""

import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def definitions(tree):
    """Module functions and class methods; nested local callbacks are not entries."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            yield from definitions(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def collect(contract):
    anchors = {}
    for key, spec in contract["source_refs"].items():
        path = ROOT / spec["path"]
        source = path.read_text()
        nodes = [n for n in definitions(ast.parse(source)) if n.name == spec["function"]]
        if len(nodes) != 1:
            raise ValueError(f"Non-unique/missing function: {key}")
        node = nodes[0]
        segment = ast.get_source_segment(source, node)
        for needle in spec["contains"]:
            if needle not in segment:
                raise ValueError(f"Missing source edge: {key}: {needle}")
        anchors[key] = {"path": spec["path"], "function": node.name,
                        "line": node.lineno, "end_line": node.end_lineno,
                        "function_sha256": hashlib.sha256(segment.encode()).hexdigest(),
                        "contains": spec["contains"]}
    prune_calls = []
    for folder in (ROOT / "src", ROOT / "tests/core"):
        for path in sorted(folder.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "prune":
                    prune_calls.append({"path": str(path.relative_to(ROOT)), "line": node.lineno,
                                        "expression": ast.unparse(node)})
    return {
        "schema": 1,
        "anchors": anchors,
        "prune_attribute_calls": prune_calls,
        "product_source_hashes": {str(p.relative_to(ROOT)): digest(p)
                                  for p in sorted((ROOT / "src").rglob("*.py"))},
        "upstream_hashes": {name: digest(ROOT / name) for name in contract["upstream_files"]},
    }


if __name__ == "__main__":
    contract = json.loads((HERE / "contract.json").read_text())
    print(json.dumps(collect(contract), indent=2, sort_keys=True))
