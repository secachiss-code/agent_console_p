"""Plan notes in the Obsidian vault, both ways.

The core writes `plans/<plan>/<KEY>.md`. A person edits the frontmatter, and the three-way merge in the domain decides
what is accepted: an accepted field goes back to the plan board, a refused one is written back as the core has it.
The body under the frontmatter belongs to the person after the first write.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final, override

import msgspec
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from aconsole.core.domain.vault_sync import Note, Owners, merge
from aconsole.core.infrastructure.vault.writer import VaultWriter, dump_front, load_front, split_note

_LOG = logging.getLogger(__name__)
_PLANS: Final = "plans"
_NOTE: Final = ".md"
_DEPTH: Final = 3
_LIST_FIELDS: Final = frozenset({"depends_on", "paths", "acceptance", "tags"})
_SHARED_FIELDS: Final = (
    "title", "objective", "acceptance", "review_required", "status", "owner_agent", "depends_on", "paths",
)
_TEXT_FIELDS: Final = ("status", "owner_agent", "lease", "updated", "title", "objective")


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeNote:
    """One node as the core wants it on disk, and what the merge needs to judge an edit."""

    key: str
    note: Note
    owners: Owners


class NoteBoard(Protocol):
    """The part of the plan board this module needs."""

    def plan_ids(self) -> tuple[str, ...]: ...

    def node_notes(self, plan_id: str) -> tuple[NodeNote, ...]: ...

    def apply_note(self, plan_id: str, key: str, note: Note) -> None: ...


class BaseStore(Protocol):
    """The frontmatter last synced for each note, so an edit made while the core was down still counts."""

    def load(self) -> dict[str, Note]: ...

    def save(self, note_ref: str, note: Note) -> None: ...


@final
class VaultSync:
    """Runs on one thread of its own: `publish` and the watcher's calls must not overlap."""

    def __init__(
        self,
        board: NoteBoard,
        *,
        writer: VaultWriter | None = None,
        bases: BaseStore | None = None,
    ) -> None:
        self._board: NoteBoard = board
        self._writer: VaultWriter = VaultWriter() if writer is None else writer
        self._bases: BaseStore | None = bases
        self._synced: dict[str, Note] = {} if bases is None else bases.load()
        self._root: Path | None = None
        self._observer: _Watch | None = None
        self.on_edit: Callable[[str], None] | None = None

    def use_bases(self, bases: BaseStore) -> None:
        """The store came up after this object did."""
        self._bases = bases
        self._synced = bases.load()

    @property
    def root(self) -> Path | None:
        return self._root

    def use_root(self, root: Path | None) -> None:
        """Change the vault. A vault that does not exist is not created: the user chooses where notes go."""
        wanted = root if root is not None and root.is_absolute() and root.is_dir() else None
        if wanted == self._root:
            return
        self.stop()
        self._root = wanted
        if wanted is not None:
            (wanted / _PLANS).mkdir(exist_ok=True)
            self._observer = _Watch(wanted / _PLANS, self._heard)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer = None

    def note_ref(self, plan_id: str, key: str) -> str | None:
        return None if self._root is None else f"{_PLANS}/{plan_id}/{key}{_NOTE}"

    def resolve(self, note_ref: str) -> Path | None:
        """The file behind a note reference. A reference that leaves the vault resolves to nothing."""
        if self._root is None:
            return None
        root = self._root.resolve()
        path = (root / note_ref).resolve()
        return path if path.is_relative_to(root) and path.is_file() else None

    def publish_all(self) -> None:
        for plan_id in self._board.plan_ids():
            self.publish(plan_id)

    def publish(self, plan_id: str) -> None:
        """Bring every note of the plan and the board together."""
        if self._root is None:
            return
        for node in self._board.node_notes(plan_id):
            self._sync_node(plan_id, node)

    def _sync_node(self, plan_id: str, node: NodeNote) -> None:
        ref = f"{_PLANS}/{plan_id}/{node.key}{_NOTE}"
        path = self._path(ref)
        if not path.is_file():
            self._write(ref, path, node.note, _first_body(node.note))
            return
        text = path.read_text(encoding="utf-8")
        front, body = split_note(text)
        user = _parsed(front)
        if user is None:
            _LOG.warning("the frontmatter of %s does not parse; the core's copy replaces it", ref)
            self._write(ref, path, node.note, body)
            return
        decision = merge(self._synced.get(ref, user), user, node.note, node.owners)
        final = decision.apply
        if _edited(final, node.note):
            self._board.apply_note(plan_id, node.key, final)
        self._write(ref, path, final, body)

    def _write(self, ref: str, path: Path, note: Note, body: str) -> None:
        _ = self._writer.write(path, f"---\n{dump_front(_front(note))}---\n{body}")
        self._synced[ref] = note
        if self._bases is not None:
            self._bases.save(ref, note)

    def _path(self, ref: str) -> Path:
        root = self._root
        if root is None:
            message = "no vault"
            raise RuntimeError(message)
        return root / ref

    def _heard(self, path: Path) -> None:
        """The watcher's thread. A file the core itself just wrote is not an edit."""
        root = self._root
        on_edit = self.on_edit
        if root is None or on_edit is None or path.suffix != _NOTE:
            return
        try:
            parts = path.relative_to(root).parts
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            return
        if len(parts) == _DEPTH and parts[0] == _PLANS and not self._writer.is_echo(path, text):
            on_edit(parts[1])


class _Handler(FileSystemEventHandler):
    def __init__(self, heard: Callable[[Path], None]) -> None:
        super().__init__()
        self._heard: Callable[[Path], None] = heard

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in {"modified", "created", "moved"}:
            return
        raw = event.dest_path if event.event_type == "moved" else event.src_path
        self._heard(Path(str(raw)))


@final
class _Watch:
    def __init__(self, directory: Path, heard: Callable[[Path], None]) -> None:
        self._observer: BaseObserver = Observer()
        _ = self._observer.schedule(_Handler(heard), str(directory), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=2)


def _first_body(note: Note) -> str:
    return f"\n# {note.title}\n\n## Цель\n\n{note.objective}\n\n## Заметки\n\n"


def _front(note: Note) -> dict[str, object]:
    return {
        "title": note.title,
        "status": note.status,
        "owner_agent": note.owner_agent,
        "depends_on": [f"[[{key}]]" for key in note.depends_on],
        "paths": list(note.paths),
        "acceptance": list(note.acceptance),
        "review_required": note.review_required,
        "objective": note.objective,
        "tags": list(note.tags),
        "lease": note.lease,
        "updated": note.updated,
    }


def _parsed(front: str) -> Note | None:
    """A note from frontmatter. Fields a person removed read as empty; anything else unreadable refuses the file."""
    values = load_front(front)
    if values is None:
        return None
    texts: dict[str, str] = {}
    for name in _TEXT_FIELDS:
        value = values.get(name, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            return None
        texts[name] = value
    lists: dict[str, tuple[str, ...]] = {}
    for name in _LIST_FIELDS:
        value = values.get(name)
        try:
            lists[name] = () if value is None else msgspec.convert(value, tuple[str, ...], strict=False)
        except msgspec.ValidationError:
            return None
    flag = values.get("review_required", False)
    if not isinstance(flag, bool):
        return None
    return Note(
        status=texts["status"],
        owner_agent=texts["owner_agent"],
        depends_on=lists["depends_on"],
        paths=lists["paths"],
        acceptance=lists["acceptance"],
        review_required=flag,
        tags=lists["tags"],
        lease=texts["lease"],
        updated=texts["updated"],
        title=texts["title"],
        objective=texts["objective"],
    )


def _edited(final: Note, app: Note) -> bool:
    """Whether the merge kept something the board does not have. App-owned fields never count."""
    return any(
        getattr(final, name) != getattr(app, name)
        for name in _SHARED_FIELDS
    )
