"""Cached local command search with a separate argument step."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import CommandResult, PaletteItem, PaletteQuery  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.i18n import core_message  # noqa: E402
from aconsole.ui.viewmodels.palette_vm import PaletteViewModel  # noqa: E402
from aconsole.ui.views.palette.messages import _ as translate  # noqa: E402


class PaletteDialog(Adw.Dialog):
    def __init__(self, client: CoreClient, on_result: Callable[[CommandResult], None]) -> None:
        super().__init__(title=translate("palette.title"), content_width=680, content_height=460)
        self.viewmodel: PaletteViewModel = PaletteViewModel(client)
        self._on_result: Callable[[CommandResult], None] = on_result
        self._generation: int = 0
        self._opened: bool = False
        self._argument_mode: bool = False
        self._items: tuple[PaletteItem, ...] = ()
        self._choices: tuple[tuple[str, str], ...] = ()
        self.worker: asyncio.Task[None] | None = None
        self._execution_task: asyncio.Task[None] | None = None
        self._setup_view()
        _ = self.connect("closed", self._closed)

    def _setup_view(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.back_button: Gtk.Button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.set_tooltip_text(translate("palette.back"))
        self.back_button.set_visible(False)
        _ = self.back_button.connect("clicked", self._back_clicked)
        header.pack_start(self.back_button)
        toolbar.add_top_bar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("ac-palette")
        self.search_entry: Gtk.SearchEntry = Gtk.SearchEntry(placeholder_text=translate("palette.search"))
        self.search_entry.set_search_delay(0)
        _ = self.search_entry.connect("changed", self._search_changed)
        _ = self.search_entry.connect("activate", self._search_activate)
        box.append(self.search_entry)
        self.stack: Gtk.Stack = Gtk.Stack(vexpand=True)
        self.results_list: Gtk.ListBox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results_list.add_css_class("boxed-list")
        self.results_list.set_header_func(self._command_header)
        _ = self.results_list.connect("row-activated", self._command_activated)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.results_list)
        _ = self.stack.add_named(scroll, "commands")
        self._setup_arguments()
        box.append(self.stack)
        self.status_label: Gtk.Label = Gtk.Label(xalign=0, wrap=True)
        self.status_label.add_css_class("ac-summary-detail")
        box.append(self.status_label)
        toolbar.set_content(box)
        self.set_child(toolbar)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _ = keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)

    def _setup_arguments(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.argument_entry: Gtk.Entry = Gtk.Entry()
        _ = self.argument_entry.connect("changed", self._argument_changed)
        _ = self.argument_entry.connect("activate", self._argument_activate)
        box.append(self.argument_entry)
        self.argument_list: Gtk.ListBox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.argument_list.add_css_class("boxed-list")
        _ = self.argument_list.connect("row-activated", self._argument_activated)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.argument_list)
        box.append(scroll)
        run = Gtk.Button(label=translate("palette.run"))
        _ = run.connect("clicked", self._run_clicked)
        box.append(run)
        _ = self.stack.add_named(box, "arguments")

    def open(self, context: PaletteQuery, parent: Gtk.Widget) -> None:
        if self._opened:
            self.present(parent)
            return
        self._opened = True
        self._generation += 1
        self._argument_mode = False
        self.search_entry.set_visible(True)
        self.search_entry.set_text("")
        self.back_button.set_visible(False)
        self.back_button.set_sensitive(True)
        self.stack.set_sensitive(True)
        self.stack.set_visible_child_name("commands")
        self._clear(self.results_list)
        self.status_label.set_label(translate("palette.loading"))
        self.present(parent)
        _ = self.search_entry.grab_focus()
        self.worker = spawn_task(self._load(context, self._generation))

    async def _load(self, context: PaletteQuery, generation: int) -> None:
        try:
            await self.viewmodel.open(context)
        except Exception:  # noqa: BLE001 -- preserve the dialog and show a localized load failure
            if generation == self._generation:
                self.status_label.set_label(translate("palette.error"))
            return
        if generation == self._generation and self._opened:
            self.viewmodel.search(self.search_entry.get_text())
            self._render_commands()

    @staticmethod
    def _clear(box: Gtk.ListBox) -> None:
        while (row := box.get_row_at_index(0)) is not None:
            box.remove(row)

    @staticmethod
    def _row(title: str, subtitle: str | None = None, shortcut: str | None = None) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=12)
        box.add_css_class("ac-palette-row")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END))
        if subtitle:
            label = Gtk.Label(label=subtitle, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            label.add_css_class("ac-summary-detail")
            text.append(label)
        box.append(text)
        if shortcut:
            box.append(Gtk.Label(label=shortcut))
        return Gtk.ListBoxRow(child=box)

    def _render_commands(self) -> None:
        self._clear(self.results_list)
        self._items = self.viewmodel.items[:100]
        for item in self._items:
            row = self._row(item.title, item.subtitle if item.enabled else item.disabled_reason, item.shortcut)
            row.set_sensitive(item.enabled)
            row.set_tooltip_text(item.disabled_reason or item.subtitle)
            self.results_list.append(row)
        first = next((index for index, item in enumerate(self._items) if item.enabled), None)
        if first is not None:
            self.results_list.select_row(self.results_list.get_row_at_index(first))
        empty_key = "palette.empty" if self.viewmodel.query else "palette.recent_empty"
        self.status_label.set_label("" if self._items else translate(empty_key))

    def _command_header(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        index = row.get_index()
        if not 0 <= index < len(self._items):
            row.set_header(None)
            return
        previous = before.get_index() if before is not None else -1
        kind = self._items[index].kind
        if previous >= 0 and self._items[previous].kind == kind:
            row.set_header(None)
            return
        heading = Gtk.Label(label=translate(f"palette.kind.{kind.value}").upper(), xalign=0)
        heading.add_css_class("ac-palette-group")
        row.set_header(heading)

    def _search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if not self._argument_mode:
            self.viewmodel.search(self.search_entry.get_text())
            self._render_commands()

    def activate_selected(self) -> None:
        row = self.results_list.get_selected_row()
        index = row.get_index() if row is not None else -1
        if not 0 <= index < len(self._items) or not self._items[index].enabled:
            return
        item = self._items[index]
        self.viewmodel.select(item.id)
        if item.argument is None:
            self._queue_execute(None)
            return
        self._argument_mode = True
        self.back_button.set_visible(True)
        self.search_entry.set_visible(False)
        self.argument_entry.set_placeholder_text(item.argument.label)
        self.argument_entry.set_text("")
        self.stack.set_visible_child_name("arguments")
        self._render_arguments()
        _ = self.argument_entry.grab_focus()

    def _render_arguments(self) -> None:
        self._clear(self.argument_list)
        item = self.viewmodel.selected
        choices = item.argument.choices if item is not None and item.argument is not None else None
        query = self.argument_entry.get_text().casefold()
        self._choices = tuple(choice for choice in choices or () if query in choice[1].casefold())
        for _value, title in self._choices:
            self.argument_list.append(self._row(title))
        self.argument_list.select_row(self.argument_list.get_row_at_index(0))

    def activate_argument(self) -> None:
        if not self._argument_mode:
            return
        item = self.viewmodel.selected
        if item is None or item.argument is None:
            return
        if item.argument.choices:
            row = self.argument_list.get_selected_row()
            index = row.get_index() if row is not None else -1
            if not 0 <= index < len(self._choices):
                return
            argument = self._choices[index][0]
        else:
            argument = self.argument_entry.get_text()
        self._queue_execute(argument)

    def _queue_execute(self, argument: str | None) -> None:
        if self._execution_task is None or self._execution_task.done():
            self._execution_task = spawn_task(self._execute(argument, self._generation))
            self.worker = self._execution_task

    async def _execute(self, argument: str | None, generation: int) -> None:
        self.stack.set_sensitive(False)
        self.back_button.set_sensitive(False)
        try:
            result = await self.viewmodel.execute(argument)
            if result is not None and generation == self._generation and self._opened:
                if result.ok:
                    _ = self.close()
                    # `closed` fires only when the sheet's animation ends, which needs frames. The command is done now.
                    self._mark_closed()
                else:
                    self.status_label.set_label(
                        core_message(result.toast) if result.toast else translate("palette.execute_error"),
                    )
                self._on_result(result)
        except Exception:  # noqa: BLE001 -- retain the selected command and allow deliberate retry
            if generation == self._generation:
                self.status_label.set_label(translate("palette.execute_error"))
        finally:
            if generation == self._generation:
                self.stack.set_sensitive(True)
                self.back_button.set_sensitive(True)

    def go_back(self) -> None:
        self.viewmodel.back()
        self._argument_mode = False
        self.search_entry.set_visible(True)
        self.back_button.set_visible(False)
        self.stack.set_visible_child_name("commands")
        self._render_commands()
        _ = self.search_entry.grab_focus()

    def _back_clicked(self, _button: Gtk.Button) -> None:
        self.go_back()

    def _search_activate(self, _entry: Gtk.SearchEntry) -> None:
        self.activate_selected()

    def _command_activated(self, _box: Gtk.ListBox, _row: Gtk.ListBoxRow) -> None:
        self.results_list.select_row(_row)
        self.activate_selected()

    def _argument_activate(self, _entry: Gtk.Entry) -> None:
        self.activate_argument()

    def _argument_activated(self, _box: Gtk.ListBox, _row: Gtk.ListBoxRow) -> None:
        self.argument_list.select_row(_row)
        self.activate_argument()

    def _run_clicked(self, _button: Gtk.Button) -> None:
        self.activate_argument()

    def _argument_changed(self, _entry: Gtk.Entry) -> None:
        self._render_arguments()

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._mark_closed()

    def _mark_closed(self) -> None:
        if not self._opened:
            return
        self._opened = False
        self._generation += 1
        self.viewmodel.close()

    def _key_pressed(self, _keys: Gtk.EventControllerKey, key: int, _code: int, _state: Gdk.ModifierType) -> bool:
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self._argument_mode:
                self.activate_argument()
            else:
                self.activate_selected()
            return True
        if key == Gdk.KEY_Escape and self._argument_mode:
            self.go_back()
            return True
        if key in (Gdk.KEY_Up, Gdk.KEY_Down):
            box = self.argument_list if self._argument_mode else self.results_list
            selected = box.get_selected_row()
            index = selected.get_index() if selected is not None else -1
            next_row = box.get_row_at_index(max(0, index + (1 if key == Gdk.KEY_Down else -1)))
            if next_row is not None:
                box.select_row(next_row)
            return True
        return False
