"""The contract checks from docs/contracts/README.md §4."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import msgspec
from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator

from aconsole.contracts.errors import ERRORS
from aconsole.contracts.generate import documents, struct_types
from aconsole.contracts.methods import METHODS


def check_schema_regen(repo: Path) -> list[str]:
    problems: list[str] = []
    for relative, text in documents().items():
        if not relative.startswith("contracts/schema/"):
            continue
        path = repo / relative
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            problems.append(f"schema out of date: {relative}")
    return problems


def check_breaking(repo: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted((repo / "contracts" / "schema").rglob("*.schema.json")):
        relative = path.relative_to(repo).as_posix()
        previous = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if previous.returncode != 0:
            continue
        problems.extend(_breaking(relative, json.loads(previous.stdout), json.loads(path.read_text(encoding="utf-8"))))
    return problems


def _breaking(name: str, old: dict[str, object], new: dict[str, object]) -> list[str]:
    old_defs = old.get("$defs")
    new_defs = new.get("$defs")
    if not isinstance(old_defs, dict) or not isinstance(new_defs, dict):
        return []
    problems: list[str] = []
    for key, old_schema in old_defs.items():
        new_schema = new_defs.get(key)
        if isinstance(old_schema, dict) and isinstance(new_schema, dict):
            problems.extend(_schema_break(f"{name}:{key}", old_schema, new_schema))
    return problems


def _schema_break(name: str, old: dict[str, object], new: dict[str, object]) -> list[str]:
    problems: list[str] = []
    if old.get("type") != new.get("type"):
        problems.append(f"breaking type change in {name}")
    old_required = set(old.get("required", []) if isinstance(old.get("required"), list) else [])
    new_required = set(new.get("required", []) if isinstance(new.get("required"), list) else [])
    removed = old_required - set(new.get("properties", {}) if isinstance(new.get("properties"), dict) else [])
    added = new_required - old_required
    if removed:
        problems.append(f"breaking removed required field in {name}: {sorted(removed)}")
    if added:
        problems.append(f"breaking new required field in {name}: {sorted(added)}")
    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list) and set(map(str, old_enum)) != set(map(str, new_enum)):
        problems.append(f"breaking enum change in {name}")
    return problems


def check_draft_schema(repo: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted((repo / "contracts" / "schema").rglob("*.schema.json")):
        try:
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
        except SchemaError as exc:
            problems.append(f"{path.name}: {exc}")
    return problems


def check_golden(repo: Path) -> list[str]:
    by_name = {struct.__name__: struct for struct in struct_types()}
    golden = repo / "tests" / "contracts" / "golden"
    files = sorted(golden.glob("*.json"))
    problems: list[str] = []
    if len(files) < len(by_name):
        problems.append(f"golden files {len(files)} < struct types {len(by_name)}")
    for path in files:
        struct = by_name.get(path.stem)
        if struct is None:
            problems.append(f"golden without a struct: {path.name}")
            continue
        value = msgspec.json.decode(path.read_bytes(), type=struct)
        again = msgspec.json.decode(msgspec.json.encode(value), type=struct)
        if again != value:
            problems.append(f"golden does not round-trip: {path.name}")
    return problems


def check_compat_matrix(repo: Path) -> list[str]:
    """Goldens are the only published payloads. They must still decode."""
    return [item for item in check_golden(repo) if item.startswith("golden")]


def check_error_registry(repo: Path) -> list[str]:
    _ = repo
    problems: list[str] = []
    codes = [item.code for item in ERRORS]
    names = [item.name for item in ERRORS]
    if len(codes) != len(set(codes)):
        problems.append("error codes are not unique")
    if len(names) != len(set(names)):
        problems.append("error names are not unique")
    if any(not isinstance(item.retryable, bool) for item in ERRORS):
        problems.append("retryable must be set")
    known = set(names)
    for method in METHODS:
        missing = [item for item in method.errors if item not in known]
        if missing:
            problems.append(f"{method.name} names unknown errors {missing}")
    for struct in struct_types():
        tag = getattr(struct, "__struct_config__", None)
        tag_value = getattr(tag, "tag", None)
        if isinstance(tag_value, str) and tag_value.isupper() and tag_value not in known:
            problems.append(f"detail tag {tag_value} is not in the registry")
    return problems


def check_contracts_types(repo: Path) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "basedpyright"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        return ["basedpyright failed", *tail[-20:]]
    return []


def check_both_interpreters(repo: Path) -> list[str]:
    problems: list[str] = []
    probes = (
        (repo / ".venv-core" / "bin" / "python", "False"),
        (repo / ".venv-ui" / "bin" / "python", "True"),
    )
    code = "import aconsole.contracts, sys; raise SystemExit(0 if str(sys._is_gil_enabled()) == %r else 1)"
    for python, expected in probes:
        env = os.environ.copy()
        if expected == "False":
            env["PYTHON_GIL"] = "0"
        else:
            env.pop("PYTHON_GIL", None)
        proc = subprocess.run(
            [str(python), "-c", code % expected],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            problems.append(f"{python.name} import failed: {proc.stderr.strip()}")
    return problems


def check_generated_markdown(repo: Path) -> list[str]:
    relative = "docs/contracts/generated/types.md"
    text = documents()[relative]
    path = repo / relative
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        return [f"markdown out of date: {relative}"]
    return []


def check_typed_facade(repo: Path) -> list[str]:
    """`ui/client/typed_facade.py` is what `python -m aconsole.ui.client.generate` writes from METHODS."""
    from aconsole.ui.client.generate import render  # noqa: PLC0415 -- the UI client loads only for this check

    relative = "src/aconsole/ui/client/typed_facade.py"
    path = repo / relative
    if not path.is_file() or path.read_text(encoding="utf-8") != render():
        return [f"typed facade out of date: {relative} (run python -m aconsole.ui.client.generate)"]
    return []


def check_mcp_conformance(repo: Path) -> list[str]:
    """External MCP conformance needs a live server. Skip until ACONSOLE_MCP_URL is set."""
    _ = repo
    url = os.environ.get("ACONSOLE_MCP_URL")
    if not url:
        return []
    proc = subprocess.run(
        ["npx", "@modelcontextprotocol/conformance", "server", "--url", url, "--requirements", "2026-07-28"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return [f"mcp conformance failed: {proc.stderr.strip() or proc.stdout.strip()}"]
    return []


_METHOD_LINE = re.compile(r"^- \*\*`([^`]+)`\*\*")
_ERRORS_PART = re.compile(r"Ошибки: (.*?)(?= · |$)")
_CODE = re.compile(r"`([A-Z_]+)`")


def check_app_api_errors(repo: Path) -> list[str]:
    """Each APP_API §4 method line names exactly the errors its METHODS row declares (C1)."""
    documented: dict[str, set[str]] = {}
    for line in (repo / "docs/contracts/APP_API.md").read_text(encoding="utf-8").splitlines():
        found = _METHOD_LINE.match(line)
        if found is None:
            continue
        part = _ERRORS_PART.search(line)
        documented[found.group(1)] = set(_CODE.findall(part.group(1))) if part else set()
    problems: list[str] = []
    for method in METHODS:
        listed = documented.get(method.name)
        if listed is None:
            continue
        if listed != set(method.errors):
            problems.append(f"APP_API {method.name} errors {sorted(listed)} != METHODS {sorted(method.errors)}")
    return problems


CHECKS = (
    check_schema_regen,
    check_breaking,
    check_draft_schema,
    check_golden,
    check_compat_matrix,
    check_error_registry,
    check_app_api_errors,
    check_contracts_types,
    check_both_interpreters,
    check_generated_markdown,
    check_typed_facade,
    check_mcp_conformance,
)


def main(argv: list[str]) -> int:
    repo = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    problems: list[str] = []
    for check in CHECKS:
        problems.extend(check(repo))
    if problems:
        for problem in problems:
            sys.stdout.write(problem + "\n")
        return 1
    sys.stdout.write(f"check_contracts: {len(CHECKS)} checks passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
