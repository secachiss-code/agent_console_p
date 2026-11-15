"""JSON Canvas 1.0 files, durable coordinates and bounded attempt journals.

Schema for the emitted subset follows https://jsoncanvas.org/spec/1.0/.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import jsonschema
import pytest
from sqlalchemy import select

from aconsole.core.domain.vault_sync import Note, Owners
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import CanvasPosition, NodeAttempt
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.vault.canvas import AttemptLine, CanvasHistory, CanvasNode, render_canvas
from aconsole.core.infrastructure.vault.sync import NodeNote, VaultSync
from aconsole.core.infrastructure.vault.writer import VaultWriter, update_attempt
from tests.core.test_attempt_store import _attempt, _seed

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

_NOW = datetime(2026, 11, 15, tzinfo=UTC)
_SCHEMA = {
    "type": "object",
    "required": ["nodes", "edges"],
    "additionalProperties": False,
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "x", "y", "width", "height"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"enum": ["file", "group"]},
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer", "minimum": 1},
                    "height": {"type": "integer", "minimum": 1},
                    "file": {"type": "string"},
                    "label": {"type": "string"},
                    "color": {"enum": ["1", "2", "3", "4", "5", "6"]},
                },
                "allOf": [{"if": {"properties": {"type": {"const": "file"}}}, "then": {"required": ["file"]}}],
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "fromNode", "toNode"],
                "properties": {
                    "id": {"type": "string"},
                    "fromNode": {"type": "string"},
                    "toNode": {"type": "string"},
                    "fromSide": {"enum": ["top", "right", "bottom", "left"]},
                    "toSide": {"enum": ["top", "right", "bottom", "left"]},
                    "toEnd": {"enum": ["none", "arrow"]},
                    "label": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
}


def _nodes() -> tuple[CanvasNode, ...]:
    return (
        CanvasNode(id="l0", key="CORE-0", title="Ядро", status="pending", level=0, depends_on=("CORE-1", "CORE-2")),
        CanvasNode(id="one", key="CORE-1", title="First", status="done", parent="CORE-0"),
        CanvasNode(id="two", key="CORE-2", title="Second", status="blocked", parent="CORE-0", depends_on=("CORE-1",)),
    )


def test_schema_groups_edges_and_saved_positions() -> None:
    raw = render_canvas("plan", _nodes(), {"one": (-500, 100), "two": (600, 800)})
    data = json.loads(raw)
    jsonschema.validate(data, _SCHEMA)
    group, first, second = data["nodes"]
    assert group["type"] == "group"
    assert first["file"] == "plans/plan/CORE-1.md"
    assert (first["x"], first["y"], first["color"]) == (-500, 100, "4")
    assert second["color"] == "1"
    for node in (first, second):
        assert group["x"] < node["x"]
        assert group["y"] < node["y"]
        assert group["x"] + group["width"] > node["x"] + node["width"]
        assert group["y"] + group["height"] > node["y"] + node["height"]
    assert len(data["edges"]) == 1
    assert (data["edges"][0]["fromNode"], data["edges"][0]["toNode"]) == ("one", "two")
    assert render_canvas("plan", tuple(reversed(_nodes()))) == render_canvas("plan", _nodes())


def test_legacy_nodes_and_empty_plan_are_valid() -> None:
    jsonschema.validate(json.loads(render_canvas("plan", ())), _SCHEMA)
    legacy = (CanvasNode(id="one", key="OLD-1", title="Old", status="pending"),)
    data = json.loads(render_canvas("plan", legacy))
    jsonschema.validate(data, _SCHEMA)
    assert data["nodes"][0]["type"] == "file"


@pytest.mark.parametrize("bad", ["../escape", "/root", "..", ".", "", "x\\y", "x\x00y"])
def test_canvas_paths_stay_in_the_vault(bad: str) -> None:
    with pytest.raises(ValueError, match="component"):
        render_canvas(bad, _nodes())
    with pytest.raises(ValueError, match="component"):
        render_canvas("plan", (replace(_nodes()[1], key=bad),))


def test_duplicate_canvas_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        render_canvas("plan", (_nodes()[0], replace(_nodes()[1], id="l0")))


class _Board:
    def plan_ids(self) -> tuple[str, ...]:
        return ("plan",)

    def node_notes(self, _plan_id: str) -> tuple[NodeNote, ...]:
        return tuple(
            NodeNote(
                key=node.key,
                node_id=node.id,
                level=node.level,
                parent=node.parent,
                note=Note(title=node.title, status=node.status, depends_on=node.depends_on),
                owners=Owners(),
            )
            for node in _nodes()
        )

    def apply_note(self, _plan_id: str, _key: str, _note: Note) -> None:
        pass


def test_sync_writes_canvas_atomically_without_echo_edits(tmp_path: Path) -> None:
    writer = VaultWriter()
    sync = VaultSync(_Board(), writer=writer)
    sync.use_root(tmp_path)
    changes: list[str] = []
    sync.on_edit = changes.append
    try:
        sync.publish("plan")
        path = tmp_path / "plans/plan/_plan.canvas"
        before = path.stat().st_mtime_ns
        jsonschema.validate(json.loads(path.read_text()), _SCHEMA)
        sync.publish("plan")
        assert path.stat().st_mtime_ns == before
        assert writer.is_echo(path, path.read_text())
        sync._heard(path)
        assert changes == []
    finally:
        sync.stop()


def test_history_reads_real_positions_and_attempts_without_extra_notes(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    writer.call(_seed)
    database = AttemptDatabase(writer)
    history = CanvasHistory(writer=writer)

    def position(session: Session) -> None:
        session.add(CanvasPosition(plan_id="plan", node_id="n", x=-12, y=37, user_moved=True, updated_at=_NOW))

    writer.call(position)
    root = tmp_path / "vault"
    root.mkdir()

    class Board(_Board):
        def node_notes(self, _plan_id: str) -> tuple[NodeNote, ...]:
            return (NodeNote(key="n", node_id="n", note=Note(title="Node"), owners=Owners()),)

    sync = VaultSync(Board())
    sync.use_history(history)
    sync.use_root(root)
    try:
        sync.publish("plan")
        files_before = set(root.rglob("*"))
        note = root / "plans/plan/n.md"
        note.write_text(note.read_text() + "\n## User notes\nkeep\n")
        for number in range(1, 5):
            database.start(_attempt(number))
            database.finish(f"attempt-{number}", "failed", now=_NOW, compact_verdict="retry\n## fake")
            sync.publish("plan")
        sync.publish("plan")
        text = note.read_text()
        assert text.count("## Попытки") == 1
        assert text.count("/4 · failed") == 4
        assert "## User notes\nkeep" in text
        assert "## fake\n" not in text
        assert set(root.rglob("*")) == files_before
        assert history.positions("plan") == {"n": (-12, 37)}
        data = json.loads((root / "plans/plan/_plan.canvas").read_text())
        assert (data["nodes"][0]["x"], data["nodes"][0]["y"]) == (-12, 37)
        assert len(history.attempts("plan")) == 4

        def check(session: Session) -> None:
            assert len(session.scalars(select(NodeAttempt)).all()) == 4

        writer.call(check)
    finally:
        sync.stop()
        writer.stop()
        engine.dispose()


def test_journal_updates_an_attempt_in_place_and_preserves_later_sections() -> None:
    text = "# Node\n\n## Цель\nkeep\n\n## Попытки\n- 1/4 · running ·  · -\n\n## User\nkeep too\n"
    line = AttemptLine(key="n", attempt_no=1, status="failed", verdict="retry", head_block="hash").render()
    updated = update_attempt(text, 1, line)
    assert "running" not in updated
    assert updated.count("1/4") == 1
    assert "## User\nkeep too\n" in updated
    assert update_attempt(updated, 1, line) == updated


def test_saved_direction_coordinates_place_unsaved_children_inside() -> None:
    data = json.loads(render_canvas("plan", _nodes(), {"l0": (-2000, -900)}))
    group, first, _ = data["nodes"]
    assert (group["x"], group["y"]) == (-2000, -900)
    assert first["x"] > group["x"]
    assert first["y"] > group["y"]


def test_invalid_plan_does_not_write_notes(tmp_path: Path) -> None:
    sync = VaultSync(_Board())
    sync.use_root(tmp_path)
    try:
        with pytest.raises(ValueError, match="component"):
            sync.publish("../escape")
        assert not (tmp_path / "escape").exists()
    finally:
        sync.stop()
