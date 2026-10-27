"""Run ruff and basedpyright on the file a Write tool just changed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LIMIT = 10_000


def _path_from(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return ""
    for key in ("path", "file_path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def summarize(path: str) -> str:
    if not path.endswith(".py"):
        return ""
    bindir = Path(sys.executable).parent
    chunks: list[str] = []
    for argv in (
        [str(bindir / "ruff"), "check", path],
        [str(bindir / "basedpyright"), path],
    ):
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            chunks.append(completed.stdout + completed.stderr)
    text = "\n".join(chunks).strip()
    return text[:LIMIT]


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    text = summarize(_path_from(payload))
    if text:
        sys.stdout.write(json.dumps({"additional_context": text}) + "\n")
    else:
        sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
