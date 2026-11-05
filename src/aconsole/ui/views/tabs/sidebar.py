"""Grouped sidebar tab list with empty state and drag-and-drop."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as translate  # noqa: E402
from aconsole.ui.views.tabs.row import TabRow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

_LOG = logging.getLogger(__name__)


def _section_compare(first: object, second: object, _data: object = None) -> Gtk.Ordering:
    if not isinstance(first, TabItem) or not isinstance(second, TabItem):
        return Gtk.Ordering.EQUAL
    left = str(first.section_key)
    right = str(second.section_key)
    if left < right:
        return Gtk.Ordering.SMALLER
    if left > right:
        return Gtk.Ordering.LARGER
    return Gtk.Ordering.EQUAL


class TabSidebar(Gtk.Stack):
    def __init__(self, tabs: TabsViewModel) -> None:
        super().__init__()
        self.tabs: TabsViewModel = tabs
        self.pending_reorder: asyncio.Task[None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_row_pressed: Callable[[], None] | None = None
        self._section_handlers: list[tuple[TabItem, int, int]] = []
        self._filter_handlers: list[tuple[TabItem, int]] = []
        self._header_bindings: dict[Gtk.ListHeader, GObject.Binding] = {}
        self._timer: int | None = None
        _ = self.connect("map", self._start_clock)
        _ = self.connect("unmap", self._stop_clock)
        self._sorter: Gtk.CustomSorter = Gtk.CustomSorter.new(_section_compare)
        self._project: str | None = None
        self._query: str = ""
        self._filter: Gtk.CustomFilter = Gtk.CustomFilter.new(self._matches)
        self._visible: Gtk.FilterListModel = Gtk.FilterListModel.new(tabs.model, self._filter)
        self._sorted: Gtk.SortListModel = Gtk.SortListModel.new(self._visible, None)
        self._sorted.set_section_sorter(self._sorter)
        self.selection: Gtk.SingleSelection = Gtk.SingleSelection.new(self._sorted)
        factory = Gtk.SignalListItemFactory()
        _ = factory.connect("setup", self._row_setup)
        _ = factory.connect("bind", self._row_bind)
        _ = factory.connect("unbind", self._row_unbind)
        self.tab_list: Gtk.ListView = Gtk.ListView(model=self.selection, factory=factory)
        self.tab_list.add_css_class("navigation-sidebar")
        self.tab_list.add_css_class("ac-tabs")
        self.tab_list.set_tooltip_text(translate("tabs.list"))
        self.tab_list.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("tabs.list"))],
        )
        headers = Gtk.SignalListItemFactory()
        _ = headers.connect("setup", self._header_setup)
        _ = headers.connect("bind", self._header_bind)
        _ = headers.connect("unbind", self._header_unbind)
        self.tab_list.set_header_factory(headers)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.tab_list)
        _ = self.add_named(scroll, "tabs")
        self.empty_page: Adw.StatusPage = Adw.StatusPage(description=translate("tabs.empty"))
        self.empty_page.add_css_class("compact")
        button = Gtk.Button(label=translate("tabs.new"))
        button.set_action_name("win.new-tab")
        self.empty_page.set_child(button)
        _ = self.add_named(self.empty_page, "empty")
        _ = tabs.model.connect("items-changed", self._model_changed)
        self._model_changed(tabs.model, 0, 0, 0)

    def _start_clock(self, _widget: Gtk.Widget) -> None:
        self.tabs.refresh_times()
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(30, self._tick)

    def _stop_clock(self, _widget: Gtk.Widget) -> None:
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self) -> bool:
        self.tabs.refresh_times()
        return GLib.SOURCE_CONTINUE

    def _model_changed(self, _model: object, _position: int, _removed: int, _added: int) -> None:
        for item, handler in self._filter_handlers:
            item.disconnect(handler)
        self._filter_handlers.clear()
        for item, key_handler, title_handler in self._section_handlers:
            item.disconnect(key_handler)
            item.disconnect(title_handler)
        self._section_handlers.clear()
        for index in range(self.tabs.store.get_n_items()):
            item = self.tabs.store.get(index)
            key_handler = item.connect("notify::section-key", self._section_changed)
            title_handler = item.connect("notify::section-title", self._section_changed)
            self._section_handlers.append((item, key_handler, title_handler))
            for name in ("title", "subtitle", "detail", "section-title", "project-id"):
                handler = item.connect(f"notify::{name}", self._search_field_changed)
                self._filter_handlers.append((item, handler))
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sync_page()
        self._sorter.changed(Gtk.SorterChange.DIFFERENT)

    def _matches(self, item: object, _data: object = None) -> bool:
        if not isinstance(item, TabItem):
            return False
        if self._project is not None and str(item.project_id) != self._project:
            return False
        fields = (item.title, item.subtitle, item.detail, item.section_title)
        return self._query in " ".join(str(value) for value in fields).casefold()

    def set_project(self, project_id: str | None) -> None:
        """Show the tabs of one project. None shows every tab."""
        if project_id != self._project:
            self._project = project_id
            self._filter.changed(Gtk.FilterChange.DIFFERENT)
            self._sync_page()

    def set_query(self, text: str) -> None:
        """Keep the tabs whose name, branch, last message or agent contain ``text``."""
        query = text.strip().casefold()
        if query != self._query:
            self._query = query
            self._filter.changed(Gtk.FilterChange.DIFFERENT)
            self._sync_page()

    def _sync_page(self) -> None:
        self.empty_page.set_description(translate("tabs.search.none" if self._query else "tabs.empty"))
        self.set_visible_child_name("empty" if self._visible.get_n_items() == 0 else "tabs")

    def _section_changed(self, _item: TabItem, _property: object) -> None:
        self._sorter.changed(Gtk.SorterChange.DIFFERENT)

    def _search_field_changed(self, _item: TabItem, _property: object) -> None:
        self._filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sync_page()

    def _row_setup(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        row = TabRow(self.tabs)
        source = Gtk.DragSource()
        source.set_actions(Gdk.DragAction.MOVE)
        _ = source.connect("prepare", self._drag_prepare, row)
        row.add_controller(source)
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        _ = target.connect("drop", self._drop, row)
        row.add_controller(target)
        press = Gtk.GestureClick()
        _ = press.connect("released", self._row_pressed)
        row.add_controller(press)
        item.set_child(row)

    def _row_pressed(self, _gesture: Gtk.GestureClick, _presses: int, _x: float, _y: float) -> None:
        if self.on_row_pressed is not None:
            self.on_row_pressed()

    def _row_bind(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        row, model = item.get_child(), item.get_item()
        if isinstance(row, TabRow) and isinstance(model, TabItem):
            row.bind_item(model)

    def _row_unbind(self, _factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
        row = item.get_child()
        if isinstance(row, TabRow):
            row.unbind_item()

    def _header_setup(self, _factory: Gtk.SignalListItemFactory, header: Gtk.ListHeader) -> None:
        label = Gtk.Label(xalign=0)
        label.add_css_class("ac-tab-section")
        header.set_child(label)

    def _header_bind(self, _factory: Gtk.SignalListItemFactory, header: Gtk.ListHeader) -> None:
        label, item = header.get_child(), header.get_item()
        if isinstance(label, Gtk.Label) and isinstance(item, TabItem):
            self._header_unbind(_factory, header)
            self._header_bindings[header] = item.bind_property(
                "section-title", label, "label", GObject.BindingFlags.SYNC_CREATE,
            )

    def _header_unbind(self, _factory: Gtk.SignalListItemFactory, header: Gtk.ListHeader) -> None:
        binding = self._header_bindings.pop(header, None)
        if binding is not None:
            binding.unbind()

    def _drag_prepare(
        self, _source: Gtk.DragSource, _x: float, _y: float, row: TabRow,
    ) -> Gdk.ContentProvider | None:
        if row.tab_id is None:
            return None
        return Gdk.ContentProvider.new_for_value(row.tab_id)

    def _drop(self, _target: Gtk.DropTarget, value: object, _x: float, y: float, row: TabRow) -> bool:
        return isinstance(value, str) and row.tab_id is not None and self.handle_drop(
            value, row.tab_id, after=y >= row.get_height() / 2,
        )

    def handle_drop(self, source_id: str, target_id: str, *, after: bool) -> bool:
        if self.pending_reorder is not None and not self.pending_reorder.done():
            return False
        rows = [self.tabs.store.get(index) for index in range(self.tabs.store.get_n_items())]
        source = next((row for row in rows if row.get_property("tab-id") == source_id), None)
        target = next((row for row in rows if row.get_property("tab-id") == target_id), None)
        if source is None or target is None or source is target:
            return False
        if source.get_property("section-key") != target.get_property("section-key"):
            return False
        remaining = [row for row in rows if row is not source]
        position = remaining.index(target) + int(after)
        self.pending_reorder = spawn_task(self._reorder(source_id, position))
        return True

    async def _reorder(self, source_id: str, position: int) -> None:
        try:
            await self.tabs.reorder(source_id, position)
        except Exception:  # noqa: BLE001 -- the store may already reflect a partial reorder
            _LOG.warning("Could not finish reordering tabs", exc_info=True)
            if self.on_error is not None:
                self.on_error(translate("tabs.reorder_error"))
