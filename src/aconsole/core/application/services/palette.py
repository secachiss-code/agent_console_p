"""Current navigation catalog; execution revalidates targets before opening them."""
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Final

import msgspec

from aconsole.contracts.app import (
    AppSnapshot,
    CommandResult,
    EmptyResult,
    PageRef,
    PaletteExecuteParams,
    PaletteItem,
    PaletteItemsResult,
    PaletteQuery,
    ResetRankingParams,
)
from aconsole.contracts.common import NoteRef, PaletteKind

PALETTE_METHODS: Final = frozenset({"palette/items", "palette/execute", "palette/resetRanking"})


@dataclass(frozen=True)
class _Entry:
    item: PaletteItem
    result: CommandResult | None = None
    note: NoteRef | None = None


class PaletteService:
    def __init__(
        self, snapshot: Callable[[], AppSnapshot], note_uri: Callable[[str], str],
        note_ref: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._snapshot: Callable[[], AppSnapshot] = snapshot
        self._note_uri: Callable[[str], str] = note_uri
        self._note_ref: Callable[[str, str], str | None] | None = note_ref
        self._usage: dict[str, float] = {}
        self._lock: RLock = RLock()

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        with self._lock:
            if method == "palette/items":
                entries = self._catalog(msgspec.json.decode(raw, type=PaletteQuery))
                return PaletteItemsResult(items=tuple(entry.item for entry in entries.values()))
            if method == "palette/resetRanking":
                reset = msgspec.json.decode(raw, type=ResetRankingParams)
                if reset.item_id is None:
                    self._usage.clear()
                else:
                    _ = self._usage.pop(reset.item_id, None)
                return EmptyResult()
            params = msgspec.json.decode(raw, type=PaletteExecuteParams)
            if params.argument is not None:
                message = "navigation commands do not accept arguments"
                raise ValueError(message)
            entries = self._catalog(PaletteQuery(tab_id=params.tab_id, project_id=params.project_id))
            entry = entries.get(params.item_id)
            if entry is None:
                return CommandResult(ok=False, toast="This palette item is no longer available")
            result = entry.result
            if entry.note is not None:
                try:
                    uri = self._note_uri(str(entry.note))
                except ValueError:
                    return CommandResult(ok=False, toast="This note is no longer available")
                result = CommandResult(ok=True, toast=None, open_uri=uri)
            if result is None:
                return CommandResult(ok=False, toast="This palette item is unavailable")
            # Ranking is bounded to the live catalog, rather than retaining deleted tab/node IDs forever.
            self._usage = {key: score for key, score in self._usage.items() if key in entries}
            self._usage[params.item_id] = self._usage.get(params.item_id, 0.0) + 1.0
            return result

    def _catalog(self, query: PaletteQuery) -> dict[str, _Entry]:  # noqa: C901 -- five navigation sources share validated context
        snapshot = self._snapshot()
        tab = next((row for row in snapshot.tabs if row.id == query.tab_id), None)
        if query.tab_id is not None and tab is None:
            message = "unknown tab context"
            raise ValueError(message)
        project = query.project_id or (None if tab is None else tab.project_id) or snapshot.active_project
        if project is not None and not any(row.id == project for row in snapshot.projects):
            message = "unknown project context"
            raise ValueError(message)
        if tab is not None and tab.project_id != project:
            message = "tab does not belong to project"
            raise ValueError(message)
        entries: dict[str, _Entry] = {}

        def add(
            key: str, kind: PaletteKind, title: str, subtitle: str | None,
            target: CommandResult | NoteRef,
        ) -> None:
            entries[key] = _Entry(PaletteItem(
                id=key, kind=kind, title=title, subtitle=subtitle, keywords=(kind.value,), aliases=(),
                score_hint=self._usage.get(key, 0.0), enabled=True, disabled_reason=None, argument=None, shortcut=None,
            ), target if isinstance(target, CommandResult) else None,
                target if isinstance(target, str) else None)

        tabs = tuple(row for row in snapshot.tabs if project is None or row.project_id == project)
        for row in tabs:
            add(f"tab:{row.id}", PaletteKind.TAB, row.title, row.agent.value,
                CommandResult(ok=True, toast=None, opened_tab=row.id))
        if tab is not None:
            add("command:terminal", PaletteKind.COMMAND, "Current terminal", tab.title,
                CommandResult(ok=True, toast=None, opened_tab=tab.id))
        for plan in snapshot.plans:
            if project is not None and plan.project_id != project:
                continue
            target = CommandResult(ok=True, toast=None, open_page=PageRef(kind="plan", ref=str(plan.id)))
            add(f"command:plan:{plan.id}", PaletteKind.COMMAND, f"Open plan: {plan.title}", plan.goal, target)
            for node in plan.nodes:
                add(f"node:{node.id}", PaletteKind.PLAN_NODE, f"{node.key}: {node.title}", plan.title, target)
                ref = node.note if self._note_ref is None else self._note_ref(str(plan.id), node.key)
                if ref is not None and snapshot.vault_ok:
                    add(f"note:{ref}", PaletteKind.NOTE, node.title, str(ref), NoteRef(ref))
        tab_ids = {row.id for row in tabs}
        for view in snapshot.approvals:
            if view.tab_id not in tab_ids:
                continue
            target = CommandResult(ok=True, toast=None, opened_tab=view.tab_id)
            add(f"approval:{view.id}", PaletteKind.APPROVAL, f"Review request: {view.tool}", str(view.tab_id), target)
        if snapshot.approvals and any(view.tab_id in tab_ids for view in snapshot.approvals):
            first = next(view for view in snapshot.approvals if view.tab_id in tab_ids)
            add("command:approvals", PaletteKind.COMMAND, "Pending requests", None,
                CommandResult(ok=True, toast=None, opened_tab=first.tab_id))
        return entries
