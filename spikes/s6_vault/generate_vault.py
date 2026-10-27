"""Write a tiny Obsidian vault in the A0.8 shape for later sync tests."""

from __future__ import annotations

import sys
from pathlib import Path

TASK = """---
aconsole: 1
type: task
id: 01JB2Z00000000000000000001
plan: "[[_plan]]"
key: N-01
status: pending
owner_agent: claude
depends_on: []
paths:
  - src/aconsole/**
acceptance:
  - The note round-trips through ruamel.yaml
review_required: false
tags:
  - aconsole/task
  - status/pending
lease: ""
updated: 2026-10-24T12:00:00Z
---
# N-01 · Vault sample

## Цель
Sample task for the vault spike.

## Связи
- нет блокеров

## Журнал
- 2026-10-24 12:00 app: generated
"""

PLAN = """---
aconsole: 1
type: plan
id: 01JB2Z00000000000000000000
status: pending
updated: 2026-10-24T12:00:00Z
---
# Sample plan

## Цель
Exercise frontmatter, a wikilink, and a canvas.
"""

CANVAS = """{
  "nodes": [
    {
      "id": "01JB2Z00000000000000000001",
      "type": "file",
      "file": "N-01-vault-sample.md",
      "x": 0,
      "y": 0,
      "width": 280,
      "height": 120
    }
  ],
  "edges": []
}
"""

BASE = """filters:
  and:
    - file.hasTag("aconsole/task")
views:
  - type: table
    name: tasks
    order:
      - file.name
"""


def generate(root: Path) -> None:
    project = root / "sample"
    plan = project / "plans" / "sample-plan"
    (plan).mkdir(parents=True, exist_ok=True)
    (project / "knowledge" / "facts").mkdir(parents=True, exist_ok=True)
    (project / "_app" / "dashboards").mkdir(parents=True, exist_ok=True)
    (root / "00_Home.md").write_text("# AConsole\n\nGenerated sample vault.\n", encoding="utf-8")
    (project / "00_Index.md").write_text("# sample\n\n- [[plans/sample-plan/_plan]]\n", encoding="utf-8")
    (plan / "_plan.md").write_text(PLAN, encoding="utf-8")
    (plan / "N-01-vault-sample.md").write_text(TASK, encoding="utf-8")
    (plan / "sample-plan.canvas").write_text(CANVAS, encoding="utf-8")
    (project / "_app" / "dashboards" / "plans.base").write_text(BASE, encoding="utf-8")
    (project / "knowledge" / "facts" / "F-1-sample.md").write_text(
        "---\naconsole: 1\ntype: fact\nid: F-1\n---\n# Sample fact\n\nSource: vault spike.\n",
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path("tests/fixtures/vault/sample")
    generate(root)
    sys.stdout.write(f"vault {root}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
