"""JSON Canvas 1.0 files, durable coordinates and bounded attempt journals.

Schema for the emitted subset follows https://jsoncanvas.org/spec/1.0/.
"""

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NotRequired, TypedDict, override

import jsonschema
import msgspec
import pytest
from sqlalchemy import select

from aconsole.core.domain.vault_sync import Note, Owners
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import CanvasPosition, NodeAttempt
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.vault.canvas import AttemptLine, CanvasHistory, CanvasNode, render_canvas
from aconsole.core.infrastructure.vault.sync import NodeNote, VaultSync
from aconsole.core.infrastructure.vault.writer import VaultWriter, update_attempt
from tests.core.test_attempt_store import _attempt, _seed  # pyright: ignore[reportPrivateUsage]

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


class _CanvasItem(TypedDict):
    id: str
    type: str
    x: int
    y: int
    width: int
    height: int
    file: NotRequired[str]
    color: NotRequired[str]
    label: NotRequired[str]


class _CanvasEdge(TypedDict):
    id: str
    fromNode: str
    toNode: str


class _CanvasDocument(TypedDict):
    nodes: list[_CanvasItem]
    edges: list[_CanvasEdge]


def _canvas(raw: str) -> _CanvasDocument:
    document = msgspec.json.decode(raw, type=dict[str, object])
    jsonschema.validate(document, _SCHEMA)
    return msgspec.convert(document, type=_CanvasDocument)


def _nodes() -> tuple[CanvasNode, ...]:
    return (
        CanvasNode(id="l0", key="CORE-0", title="Ядро", status="pending", level=0, depends_on=("CORE-1", "CORE-2")),
        CanvasNode(id="one", key="CORE-1", title="First", status="done", parent="CORE-0"),
        CanvasNode(id="two", key="CORE-2", title="Second", status="blocked", parent="CORE-0", depends_on=("CORE-1",)),
    )


def test_schema_groups_edges_and_saved_positions() -> None:
    raw = render_canvas("plan", _nodes(), {"one": (-500, 100), "two": (600, 800)})
    data = _canvas(raw)
    jsonschema.validate(data, _SCHEMA)
    group, first, second = data["nodes"]
    assert group["type"] == "group"
    assert first.get("file") == "plans/plan/CORE-1.md"
    assert (first["x"], first["y"], first.get("color")) == (-500, 100, "4")
    assert second.get("color") == "1"
    for node in (first, second):
        assert group["x"] < node["x"]
        assert group["y"] < node["y"]
        assert group["x"] + group["width"] > node["x"] + node["width"]
        assert group["y"] + group["height"] > node["y"] + node["height"]
    assert len(data["edges"]) == 1
    assert (data["edges"][0]["fromNode"], data["edges"][0]["toNode"]) == ("one", "two")
    assert render_canvas("plan", tuple(reversed(_nodes()))) == render_canvas("plan", _nodes())


def test_legacy_nodes_and_empty_plan_are_valid() -> None:
    jsonschema.validate(_canvas(render_canvas("plan", ())), _SCHEMA)
    legacy = (CanvasNode(id="one", key="OLD-1", title="Old", status="pending"),)
    data = _canvas(render_canvas("plan", legacy))
    jsonschema.validate(data, _SCHEMA)
    assert data["nodes"][0]["type"] == "file"


def test_actual_board_projects_level_parent_and_canvas_link(tmp_path: Path) -> None:
    board = PlanBoard()
    ctx = TabContext(tab_id="t", project_id="p", worktree=None, agent="codex")
    created = board.create(
        ctx,
        "Plan",
        "goal",
        [
            NodeIn(key="CORE-0", title="Core", objective="direction", level=0),
            NodeIn(key="CORE-1", title="Module", objective="module", level=1, parent="CORE-0"),
        ],
    )
    sync = VaultSync(board)
    board.note_ref = sync.note_ref
    sync.use_root(tmp_path)
    try:
        sync.publish(created.plan_id)
        notes = board.node_notes(created.plan_id)
        assert notes[0].level == 0
        assert notes[1].parent == "CORE-0"
        view = board.view(created.plan_id)
        assert view is not None
        assert str(view.canvas) == f"plans/{created.plan_id}/_plan.canvas"
        data = _canvas((tmp_path / str(view.canvas)).read_text())
        jsonschema.validate(data, _SCHEMA)
        assert [node["type"] for node in data["nodes"]] == ["group", "file"]
        assert {node["id"] for node in data["nodes"]} == {str(node.id) for node in view.nodes}
    finally:
        sync.stop()


@pytest.mark.parametrize("bad", ["../escape", "/root", "..", ".", "", "x\\y", "x\x00y"])
def test_canvas_paths_stay_in_the_vault(bad: str) -> None:
    with pytest.raises(ValueError, match="component"):
        _ = render_canvas(bad, _nodes())
    with pytest.raises(ValueError, match="component"):
        _ = render_canvas("plan", (replace(_nodes()[1], key=bad),))


def test_duplicate_canvas_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        _ = render_canvas("plan", (_nodes()[0], replace(_nodes()[1], id="l0")))


class _Board:
    def plan_ids(self) -> tuple[str, ...]:
        return ("plan",)

    def node_notes(self, plan_id: str) -> tuple[NodeNote, ...]:
        _ = plan_id
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

    def apply_note(self, plan_id: str, key: str, note: Note) -> None:
        _ = (plan_id, key, note)


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
        jsonschema.validate(_canvas(path.read_text()), _SCHEMA)
        sync.publish("plan")
        assert path.stat().st_mtime_ns == before
        assert writer.is_echo(path, path.read_text())
        sync._heard(path)  # pyright: ignore[reportPrivateUsage]
        assert changes == []
    finally:
        sync.stop()


def test_history_reads_real_positions_and_attempts_without_extra_notes(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    _ = writer.call(_seed)
    database = AttemptDatabase(writer)
    history = CanvasHistory(writer=writer)

    def position(session: Session) -> None:
        session.add(CanvasPosition(plan_id="plan", node_id="n", x=-12, y=37, user_moved=True, updated_at=_NOW))

    _ = writer.call(position)
    root = tmp_path / "vault"
    root.mkdir()

    class Board(_Board):
        @override
        def node_notes(self, plan_id: str) -> tuple[NodeNote, ...]:
            _ = plan_id
            return (NodeNote(key="n", node_id="n", note=Note(title="Node"), owners=Owners()),)

    sync = VaultSync(Board())
    sync.use_history(history)
    sync.use_root(root)
    try:
        sync.publish("plan")
        files_before = set(root.rglob("*"))
        note = root / "plans/plan/n.md"
        baseline = note.read_bytes()
        foreign = baseline + b"\n## User notes\nkeep\n"
        _ = note.write_bytes(foreign)
        for number in range(1, 5):
            database.start(_attempt(number))
            database.finish(f"attempt-{number}", "failed", now=_NOW, compact_verdict="retry\n## fake")
            sync.publish("plan")
        sync.publish("plan")
        assert note.read_bytes() == foreign
        assert len(history.attempts("plan")) == 4
        _ = note.write_bytes(baseline)
        sync.publish("plan")
        text = note.read_text()
        assert text.count("## Попытки") == 1
        assert text.count("/4 · failed") == 4
        assert "## User notes\nkeep" not in text
        assert "## fake\n" not in text
        assert set(root.rglob("*")) == files_before
        assert history.positions("plan") == {"n": (-12, 37)}
        data = _canvas((root / "plans/plan/_plan.canvas").read_text())
        assert (data["nodes"][0]["x"], data["nodes"][0]["y"]) == (-12, 37)
        assert len(history.attempts("plan")) == 4

        def check(session: Session) -> None:
            assert len(session.scalars(select(NodeAttempt)).all()) == 4

        _ = writer.call(check)
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
    data = _canvas(render_canvas("plan", _nodes(), {"l0": (-2000, -900)}))
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


def test_kahn_chain_uses_l1_dependencies_and_450_pixel_columns() -> None:
    nodes = (
        CanvasNode(
            id="root", key="IDX-0", title="Index", status="pending", level=0, depends_on=("IDX-1", "IDX-2", "IDX-3")
        ),
        CanvasNode(id="a", key="IDX-1", title="A", status="pending", parent="IDX-0"),
        CanvasNode(id="b", key="IDX-2", title="B", status="pending", parent="IDX-0", depends_on=("IDX-1",)),
        CanvasNode(id="c", key="IDX-3", title="C", status="pending", parent="IDX-0", depends_on=("IDX-2",)),
    )
    data = _canvas(render_canvas("plan", nodes))
    group, first, second, third = data["nodes"]
    assert [first["x"], second["x"], third["x"]] == [0, 450, 900]
    assert [first["y"], second["y"], third["y"]] == [0, 0, 0]
    assert group["x"] < first["x"]
    assert group["x"] + group["width"] > third["x"] + third["width"]
    assert render_canvas("plan", nodes) == render_canvas("plan", tuple(reversed(nodes)))
    jsonschema.validate(data, _SCHEMA)
    saved = _canvas(render_canvas("plan", nodes, {"b": (-99, 123)}))
    assert (saved["nodes"][2]["x"], saved["nodes"][2]["y"]) == (-99, 123)
    assert saved["nodes"][3]["x"] == 900


def test_single_barycenter_pass_orders_crossed_dependencies() -> None:
    nodes = (
        CanvasNode(id="a", key="MOD-A", title="A", status="pending"),
        CanvasNode(id="b", key="MOD-B", title="B", status="pending"),
        CanvasNode(id="c", key="MOD-C", title="C", status="pending", depends_on=("MOD-B",)),
        CanvasNode(id="d", key="MOD-D", title="D", status="pending", depends_on=("MOD-A",)),
    )
    data = _canvas(render_canvas("plan", nodes))
    first, second, crossed, direct = data["nodes"]
    assert (first["x"], second["x"]) == (0, 0)
    assert (crossed["x"], direct["x"]) == (450, 450)
    assert first["y"] < second["y"]
    assert direct["y"] < crossed["y"]
    assert crossed["y"] - direct["y"] == 240


def test_dirty_cycle_and_blocked_descendants_fall_back_to_layer_zero() -> None:
    nodes = (
        CanvasNode(id="a", key="MOD-A", title="A", status="pending", depends_on=("MOD-B",)),
        CanvasNode(id="b", key="MOD-B", title="B", status="pending", depends_on=("MOD-A",)),
        CanvasNode(id="c", key="MOD-C", title="C", status="pending", depends_on=("MOD-B",)),
        CanvasNode(id="d", key="MOD-D", title="D", status="pending", depends_on=("MOD-D",)),
        CanvasNode(id="e", key="MOD-E", title="E", status="pending", depends_on=("MISSING",)),
    )
    data = _canvas(render_canvas("plan", nodes))
    assert [node["x"] for node in data["nodes"]] == [0] * 5
    assert [node["y"] for node in data["nodes"]] == [0, 240, 480, 720, 960]
    assert render_canvas("plan", nodes) == render_canvas("plan", tuple(reversed(nodes)))
    jsonschema.validate(data, _SCHEMA)
