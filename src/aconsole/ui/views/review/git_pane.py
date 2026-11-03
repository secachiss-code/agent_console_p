"""Git & checkpoints: one project's graph, and what a selected row changed.

The list is virtual and its rows are recycled, so a long history costs only the rows on screen. A refresh keeps the
selection and the scroll position. Commit, merge, and other changes are in `git_actions`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import DiffFileResult, DiffFileView  # noqa: E402
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.contracts.git import DiffSource, GitNodeView, GitStateResult, WorktreeGitState  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreCallError  # noqa: E402
from aconsole.ui.viewmodels.git_vm import (  # noqa: E402
    Badge,
    GitViewModel,
    GraphBuilder,
    GraphRow,
    WorktreeMode,
    merge_badges,
    node_source,
    short,
    uncommitted_rows,
    worktree_of,
    worktree_source,
)
from aconsole.ui.views.review.diff_widgets import file_row, hunk_text  # noqa: E402
from aconsole.ui.views.review.git_actions import GitActions  # noqa: E402
from aconsole.ui.views.review.graph_cell import ROW_HEIGHT, GraphCell  # noqa: E402
from aconsole.ui.views.review.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

_PANE_POSITION: Final = 620
_STACK_BELOW: Final = "max-width: 900sp"
_STACKED_POSITION: Final = 200
_STACKED_FIXED_HEIGHT: Final = 64
_BODY_MIN_WIDTH: Final = 360
_BODY_MIN_HEIGHT: Final = 300
_FILES_HEIGHT: Final = 170
_KEEP_WATCHED_S: Final = 15
_NEAR_END: Final = 2.0
_MODES: Final[tuple[WorktreeMode, ...]] = ("worktree", "index", "base")
_TIME: Final = "%d.%m %H:%M"
_BADGE_CLASS: Final = {"ok": "success", "warn": "warning", "bad": "error", "info": "dim-label"}


class RowItem(GObject.Object):
    """One list row. The store holds these; the widgets are recycled."""

    def __init__(self, row: GraphRow) -> None:
        super().__init__()
        self.row: GraphRow = row


class _RowWidget(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(spacing=8)
        self.add_css_class("ac-git-row")
        self.set_size_request(-1, ROW_HEIGHT)
        self.cell: GraphCell = GraphCell()
        self.append(self.cell)
        self.title: Gtk.Label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, width_chars=18)
        self.append(self.title)
        self.pills: Gtk.Box = Gtk.Box(spacing=4)
        self.append(self.pills)
        self.when: Gtk.Label = Gtk.Label()
        self.when.add_css_class("dim-label")
        self.when.add_css_class("ac-git-when")
        self.append(self.when)

    def show_row(self, row: GraphRow, state: GitStateResult | None) -> None:
        self.cell.set_row(
            row.layout,
            snapshot_node=row.node is not None and row.node.kind == "checkpoint",
            uncommitted=row.worktree is not None,
        )
        while (child := self.pills.get_first_child()) is not None:
            self.pills.remove(child)
        if row.worktree is not None:
            self._show_worktree(row.worktree)
        elif row.node is not None:
            self._show_node(row.node, state)

    def _show_worktree(self, worktree: WorktreeGitState) -> None:
        name = worktree.branch or translate("git.detached")
        self.title.set_text(translate("git.uncommitted").format(branch=name))
        self.title.set_tooltip_text(
            translate("git.uncommitted_counts").format(
                staged=worktree.staged,
                unstaged=worktree.unstaged,
                untracked=worktree.untracked,
                conflicted=worktree.conflicted,
            )
        )
        self.when.set_text(worktree.tab_title or translate("git.closed_tab"))
        for badge in merge_badges(worktree):
            if badge.key in {"git.badge_unchecked", "git.badge_busy", "git.badge_in_progress"}:
                self.pills.append(_pill(_badge_text(badge), badge.kind))

    def _show_node(self, node: GitNodeView, state: GitStateResult | None) -> None:
        if node.kind == "checkpoint":
            text = translate("git.snapshot").format(turn=node.turn, label=node.subject)
        else:
            text = node.subject
        self.title.set_text(text)
        self.title.set_tooltip_text(f"{short(node.oid)} {node.author}")
        self.when.set_text(node.at.astimezone().strftime(_TIME))
        for ref in node.refs:
            if ref.kind != "checkpoint":
                self.pills.append(_pill(ref.name, "info", css=f"ac-git-ref-{ref.kind}"))
        for row in () if state is None else state.worktrees:
            if row.head_oid == node.oid and not row.is_main:
                for badge in merge_badges(row):
                    if badge.key in {"git.badge_ready", "git.badge_conflict", "git.badge_behind"}:
                        self.pills.append(_pill(_badge_text(badge), badge.kind))


def _badge_text(badge: Badge) -> str:
    return translate(badge.key).format(**dict(badge.values))


def _pill(text: str, kind: str, css: str | None = None) -> Gtk.Label:
    # Centred, not stretched: the row is taller than a pill, and a stretched one reads as a tile.
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class("ac-git-pill")
    label.add_css_class(_BADGE_CLASS.get(kind, "dim-label"))
    if css is not None:
        label.add_css_class(css)
    return label


class GitPane(Gtk.Box):
    def __init__(self, viewmodel: GitViewModel, on_toast: Callable[[str], None], tab: TabId | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.viewmodel: GitViewModel = viewmodel
        self.toast: Callable[[str], None] = on_toast
        self.state: GitStateResult | None = None
        self.scope: TabId | None = None
        self._scope_tabs: tuple[TabId | None, ...] = (None,)
        self._wanted_tab: TabId | None = tab
        self._version: str | None = None
        self._cursor: str | None = None
        self._builder: GraphBuilder = GraphBuilder(None)
        self._loading: bool = False
        self._generation: int = 0
        self._file_generation: int = 0
        self._file_paths: list[str] = []
        self._syncing: bool = False
        self.selected_key: str | None = None
        self.selected_path: str | None = None
        self.source: DiffSource | None = None
        self.store: Gio.ListStore = Gio.ListStore.new(RowItem)
        self.facts_banner: Adw.Banner = Adw.Banner(button_label=translate("git.facts_show"))
        self.append(self.facts_banner)
        self.append(self._build_toolbar())
        self.append(self._build_body())
        self.git_actions: GitActions = GitActions(self, self.actions, self.commit_slot, self.facts_banner)
        # Stacked, the inspector has half the height: the fixed parts give way so the commit button and diff still show.
        short = GObject.Value(GObject.TYPE_INT, _STACKED_FIXED_HEIGHT)
        self.narrow.add_setter(self.files_scroller, "min-content-height", short)
        self.narrow.add_setter(self.git_actions.message_scroller, "min-content-height", short)
        self._keep_watch: int | None = None
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.reload()

    def reload(self) -> None:
        _ = spawn_task(self.refresh())

    def _mapped(self, _widget: Gtk.Widget) -> None:
        if self._keep_watch is None:
            self._keep_watch = GLib.timeout_add_seconds(_KEEP_WATCHED_S, self._tick)
        # An edit nobody staged makes no Git event, so coming back to the page is a reason to look.
        self.reload()

    def _unmapped(self, _widget: Gtk.Widget) -> None:
        if self._keep_watch is not None:
            _ = GLib.source_remove(self._keep_watch)
            self._keep_watch = None

    def _tick(self) -> bool:
        """The core tells clients what changed, for as long as one says it is looking. This runs no git."""
        _ = spawn_task(self._keep_updates())
        return GLib.SOURCE_CONTINUE

    async def _keep_updates(self) -> None:
        with contextlib.suppress(Exception):  # the page's next load reports a lost core
            await self.viewmodel.watch()

    def _build_toolbar(self) -> Gtk.Box:
        bar = Gtk.Box(spacing=8)
        bar.add_css_class("ac-git-toolbar")
        self.scope_choice: Gtk.DropDown = Gtk.DropDown.new_from_strings([translate("git.scope_all")])
        _ = self.scope_choice.connect("notify::selected", self._scope_changed)
        bar.append(self.scope_choice)
        self.base_label: Gtk.Label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.base_label.add_css_class("dim-label")
        bar.append(self.base_label)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=translate("git.refresh"))
        _ = refresh.connect("clicked", self._refresh_clicked)
        bar.append(refresh)
        return bar

    def _refresh_clicked(self, _button: Gtk.Button) -> None:
        self.reload()

    def _build_body(self) -> Adw.BreakpointBin:
        self.selection: Gtk.SingleSelection = Gtk.SingleSelection(model=self.store, autoselect=False, can_unselect=True)
        _ = self.selection.connect("notify::selected", self._row_selected)
        factory = Gtk.SignalListItemFactory()
        _ = factory.connect("setup", self._setup_row)
        _ = factory.connect("bind", self._bind_row)
        self.list_view: Gtk.ListView = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.add_css_class("ac-git-list")
        self.scroller: Gtk.ScrolledWindow = Gtk.ScrolledWindow(child=self.list_view, hexpand=True, vexpand=True)
        _ = self.scroller.get_vadjustment().connect("value-changed", self._scrolled)
        self.body: Gtk.Paned = Gtk.Paned(
            start_child=self.scroller, end_child=self._build_inspector(), position=_PANE_POSITION, vexpand=True,
        )
        # A graph row and the inspector cannot share less than this: one above the other, both stay usable.
        self.narrow: Adw.Breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(_STACK_BELOW))
        stacked = GObject.Value(GObject.type_from_name("GtkOrientation"), Gtk.Orientation.VERTICAL)
        self.narrow.add_setter(self.body, "orientation", stacked)
        self.narrow.add_setter(self.body, "position", GObject.Value(GObject.TYPE_INT, _STACKED_POSITION))
        fit = Adw.BreakpointBin(child=self.body, width_request=_BODY_MIN_WIDTH, height_request=_BODY_MIN_HEIGHT)
        fit.add_breakpoint(self.narrow)
        return fit

    def _build_inspector(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        top = Gtk.Box(spacing=8)
        top.add_css_class("ac-git-inspector-head")
        self.inspect_title: Gtk.Label = Gtk.Label(
            label=translate("git.select"), xalign=0, hexpand=True, wrap=True, lines=2,
            ellipsize=Pango.EllipsizeMode.END,
        )
        top.append(self.inspect_title)
        self.mode_choice: Gtk.DropDown = Gtk.DropDown.new_from_strings(
            [translate(f"git.mode_{mode}") for mode in _MODES],
        )
        self.mode_choice.set_visible(False)
        _ = self.mode_choice.connect("notify::selected", self._mode_changed)
        top.append(self.mode_choice)
        box.append(top)
        self.inspect_meta: Gtk.Label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False)
        self.inspect_meta.add_css_class("dim-label")
        self.inspect_meta.add_css_class("ac-git-inspector-meta")
        box.append(self.inspect_meta)
        self.actions: Gtk.FlowBox = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=False,
            column_spacing=6,
            row_spacing=6,
            max_children_per_line=8,
        )
        self.actions.add_css_class("ac-git-actions")
        box.append(self.actions)
        self.file_list: Gtk.ListBox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.file_list.add_css_class("navigation-sidebar")
        _ = self.file_list.connect("row-selected", self._file_selected)
        self.files_scroller: Gtk.ScrolledWindow = Gtk.ScrolledWindow(
            child=self.file_list, hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self.files_scroller.set_min_content_height(_FILES_HEIGHT)
        box.append(self.files_scroller)
        self.commit_slot: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.commit_slot)
        box.append(Gtk.Separator())
        self.diff_box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.diff_box.add_css_class("ac-review-diff")
        box.append(Gtk.ScrolledWindow(child=self.diff_box, hexpand=True, vexpand=True))
        return box

    def _setup_row(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        item.set_child(_RowWidget())

    def _bind_row(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        widget = item.get_child()
        data = item.get_item()
        if isinstance(widget, _RowWidget) and isinstance(data, RowItem):
            widget.show_row(data.row, self.state)

    async def refresh(self) -> None:
        """Compare with what is shown. Unchanged tips leave the rows, selection, and scroll position alone."""
        self._generation += 1
        generation = self._generation
        try:
            state = await self.viewmodel.state()
            first = await self.viewmodel.graph(self.scope, None)
        except Exception as error:  # noqa: BLE001 -- the page stays open and says what went wrong
            if generation == self._generation:
                self.base_label.set_text(translate("review.load_error").format(error=error))
            return
        if generation != self._generation:
            return
        previous = self.state
        self.state = state
        self._show_state(state)
        same_rows = (
            first.version == self._version
            and previous is not None
            and _uncommitted_keys(previous, self.scope) == _uncommitted_keys(state, self.scope)
        )
        if same_rows:
            self._redraw_rows()
            if previous is not None and previous.revision != state.revision:
                self._reload_inspector()
            return
        self._replace_rows(state, first.nodes, first.version, first.next_cursor)

    def _show_state(self, state: GitStateResult) -> None:
        self.git_actions.show_state(state)
        self._show_meta()
        self.base_label.set_text(state.base_branch or translate("git.empty"))
        self._syncing = True
        tabs = tuple(row for row in state.worktrees if row.tab_id is not None)
        wanted: tuple[TabId | None, ...] = (None, *(row.tab_id for row in tabs))
        if wanted != self._scope_tabs:
            self._scope_tabs = wanted
            labels = [
                translate("git.scope_all"),
                *(translate("git.scope_tab").format(title=row.tab_title) for row in tabs),
            ]
            self.scope_choice.set_model(Gtk.StringList.new(labels))
        if self.scope in wanted:
            self.scope_choice.set_selected(wanted.index(self.scope))
        self._syncing = False

    def _redraw_rows(self) -> None:
        """Badges moved but the rows did not: rebind what is on screen."""
        count = self.store.get_n_items()
        self.selection.items_changed(0, count, count)

    def _replace_rows(
        self,
        state: GitStateResult,
        nodes: tuple[GitNodeView, ...],
        version: str,
        cursor: str | None,
    ) -> None:
        keep = self.selected_key
        scrolled = self.scroller.get_vadjustment().get_value()
        self._version = version
        self._cursor = cursor
        self._builder = GraphBuilder(state.base_oid)
        items = [self._builder.uncommitted(row) for row in uncommitted_rows(state, self.scope)]
        items.extend(self._builder.commit(node) for node in nodes)
        self._syncing = True
        self.store.splice(0, self.store.get_n_items(), [RowItem(row) for row in items])
        index = next((at for at, row in enumerate(items) if row.key == keep), None)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION if index is None else index)
        self._syncing = False
        self.scroller.get_vadjustment().set_value(scrolled)
        if index is None:
            self.selected_key = None
            self._clear_inspector()
        else:
            self._reload_inspector()

    def _scrolled(self, adjustment: Gtk.Adjustment) -> None:
        left = adjustment.get_upper() - adjustment.get_page_size() - adjustment.get_value()
        if left < adjustment.get_page_size() * _NEAR_END and self._cursor is not None and not self._loading:
            _ = spawn_task(self.load_more())

    async def load_more(self) -> None:
        cursor = self._cursor
        if cursor is None or self._loading:
            return
        self._loading = True
        try:
            page = await self.viewmodel.graph(self.scope, cursor)
        except CoreCallError as error:
            if error.error.message == "GIT_SNAPSHOT_STALE":
                self.toast(translate("git.stale"))
                self._version = None
                self.reload()
            else:
                self.toast(translate("review.load_error").format(error=error))
            return
        except Exception as error:  # noqa: BLE001 -- a lost core shows here, not in a crash
            self.toast(translate("review.load_error").format(error=error))
            return
        finally:
            self._loading = False
        self._cursor = page.next_cursor
        for node in page.nodes:
            self.store.append(RowItem(self._builder.commit(node)))

    def _scope_changed(self, choice: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        if self._syncing:
            return
        index = choice.get_selected()
        self.scope = self._scope_tabs[index] if index < len(self._scope_tabs) else None
        self._version = None
        self.reload()

    def selected_row(self) -> GraphRow | None:
        item = self.selection.get_selected_item()
        return item.row if isinstance(item, RowItem) else None

    def _row_selected(self, _selection: Gtk.SingleSelection, _param: GObject.ParamSpec) -> None:
        if self._syncing:
            return
        row = self.selected_row()
        self.selected_key = None if row is None else row.key
        self.selected_path = None
        self.git_actions.refresh()
        self._reload_inspector()

    def _mode_changed(self, _choice: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        if not self._syncing:
            self._reload_inspector()

    def _source_of(self, row: GraphRow) -> DiffSource | None:
        if row.node is not None:
            return node_source(row.node)
        if row.worktree is not None:
            return worktree_source(row.worktree, _MODES[min(self.mode_choice.get_selected(), len(_MODES) - 1)])
        return None

    def _reload_inspector(self) -> None:
        row = self.selected_row()
        if row is None:
            self._clear_inspector()
            return
        self.mode_choice.set_visible(row.worktree is not None)
        self.source = self._source_of(row)
        self.inspect_title.set_text(_title_of(row))
        self._show_meta()
        _ = spawn_task(self._load_files())

    def _show_meta(self) -> None:
        """Which branch and plan node this row is, and how merging it stands, in one line."""
        row = self.selected_row()
        worktree = None if row is None else worktree_of(self.state, row)
        parts: list[str] = []
        if worktree is not None:
            parts.append(translate("git.meta_branch").format(branch=worktree.branch or translate("git.detached")))
            if worktree.plan_node is not None:
                parts.append(translate("git.meta_node").format(node=worktree.plan_node))
            parts.extend(_badge_text(badge) for badge in merge_badges(worktree))
        elif row is not None and row.node is not None and row.node.plan_node is not None:
            parts.append(translate("git.meta_node").format(node=row.node.plan_node))
            if row.node.agent:
                parts.append(row.node.agent)
        self.inspect_meta.set_text("   ".join(parts))
        self.inspect_meta.set_visible(bool(parts))

    def _clear_inspector(self) -> None:
        self.source = None
        self.inspect_meta.set_visible(False)
        self.mode_choice.set_visible(False)
        self.inspect_title.set_text(translate("git.select"))
        self.file_list.remove_all()
        self._file_paths = []
        self._clear_diff()

    async def _load_files(self) -> None:
        source = self.source
        if source is None:
            return
        self._file_generation += 1
        generation = self._file_generation
        try:
            summary = await self.viewmodel.summary(source)
        except Exception as error:  # noqa: BLE001 -- the page stays open and says what went wrong
            if generation == self._file_generation:
                self._show_message(translate("review.load_error").format(error=error))
            return
        if generation == self._file_generation:
            self._show_files(summary.files)

    def _show_files(self, files: tuple[DiffFileView, ...]) -> None:
        previous = self.selected_path
        self.file_list.remove_all()
        self._file_paths = [item.path for item in files]
        if not files:
            self.selected_path = None
            self.git_actions.refresh()
            self._show_message(translate("review.no_changes"))
            return
        for item in files:
            self.file_list.append(file_row(item, self.git_actions.file_buttons(item.path)))
        at = self._file_paths.index(previous) if previous in self._file_paths else 0
        self.file_list.select_row(self.file_list.get_row_at_index(at))

    def _file_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        source = self.source
        if row is None or source is None or row.get_index() >= len(self._file_paths):
            return
        self.selected_path = self._file_paths[row.get_index()]
        self.git_actions.refresh()
        _ = spawn_task(self._load_file(source, self.selected_path))

    async def _load_file(self, source: DiffSource, path: str) -> None:
        self._file_generation += 1
        generation = self._file_generation
        try:
            detail = await self.viewmodel.file(source, path)
        except CoreCallError as error:
            if generation == self._file_generation:
                too_large = error.error.message == "DIFF_TOO_LARGE"
                self._show_message(
                    translate("review.too_large") if too_large else translate("review.load_error").format(error=error),
                )
            return
        except Exception as error:  # noqa: BLE001 -- a lost core shows here, not in a crash
            if generation == self._file_generation:
                self._show_message(translate("review.load_error").format(error=error))
            return
        if generation == self._file_generation:
            self._show_diff(detail)

    def _clear_diff(self) -> None:
        while (child := self.diff_box.get_first_child()) is not None:
            self.diff_box.remove(child)

    def _show_message(self, text: str) -> None:
        self._clear_diff()
        label = Gtk.Label(label=text, wrap=True, vexpand=True)
        label.add_css_class("dim-label")
        self.diff_box.append(label)

    def _show_diff(self, detail: DiffFileResult) -> None:
        if not detail.hunks:
            self._show_message(translate("review.binary"))
            return
        self._clear_diff()
        for hunk in detail.hunks:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.add_css_class("ac-review-hunk")
            header = Gtk.Label(label=hunk.header, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            header.add_css_class("monospace")
            header.add_css_class("ac-review-hunk-head")
            box.append(header)
            box.append(hunk_text(hunk))
            self.diff_box.append(box)
        if detail.truncated:
            note = Gtk.Label(label=translate("review.truncated"), xalign=0)
            note.add_css_class("dim-label")
            self.diff_box.append(note)


def _uncommitted_keys(state: GitStateResult, scope: TabId | None) -> tuple[str, ...]:
    return tuple(row.path for row in uncommitted_rows(state, scope))


def _title_of(row: GraphRow) -> str:
    if row.worktree is not None:
        return translate("git.uncommitted").format(branch=row.worktree.branch or translate("git.detached"))
    if row.node is not None:
        return f"{short(row.node.oid)}  {row.node.subject}"
    return ""
