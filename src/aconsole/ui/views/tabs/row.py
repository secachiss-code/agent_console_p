"""Recyclable presentation for a single sidebar tab."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import MAX_TAB_TITLE_LENGTH, TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.review.messages import _ as review_text  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as translate  # noqa: E402

_MINUTES_PER_HOUR = 60
_MINUTES_PER_DAY = 24 * 60


def _wait_text(minutes: int) -> str:
    if minutes >= _MINUTES_PER_DAY:
        return translate("tabs.wait_days").format(days=minutes // _MINUTES_PER_DAY)
    if minutes >= _MINUTES_PER_HOUR:
        return translate("tabs.wait_hours").format(hours=minutes // _MINUTES_PER_HOUR)
    return translate("tabs.wait_minutes").format(minutes=minutes)


def _git_label() -> Gtk.Label:
    label = Gtk.Label(xalign=0, visible=False)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_single_line_mode(True)
    label.set_max_width_chars(1)
    label.add_css_class("ac-tab-subtitle")
    label.add_css_class("numeric")
    return label


class TabRow(Gtk.Box):
    def __init__(self, tabs: TabsViewModel) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("ac-tab-row")
        self.tabs: TabsViewModel = tabs
        self.tab_id: str | None = None
        self._item: TabItem | None = None
        self._notify: int | None = None
        self._settings: Gtk.Settings | None = None
        self._settings_notify: int | None = None
        self.status_spinner: Gtk.Spinner = Gtk.Spinner()
        self.status_spinner.set_size_request(12, 12)
        self._status_class: str | None = None
        self.status_label: Gtk.Label = Gtk.Label()
        self.status_label.set_valign(Gtk.Align.CENTER)
        self.status_label.set_size_request(16, 16)
        self.status_label.add_css_class("ac-tab-status")
        self.append(self.status_spinner)
        self.append(self.status_label)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)
        self.title_label: Gtk.EditableLabel = Gtk.EditableLabel()
        self.title_label.set_hexpand(True)
        self.title_label.add_css_class("ac-tab-name")
        self.title_label.set_editable(False)
        self.title_label.set_tooltip_text(translate("tabs.name"))
        self.title_label.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("tabs.name"))],
        )
        _ = self.title_label.connect("notify::editing", self._editing_changed)
        self.subtitle_label: Gtk.Label = Gtk.Label(xalign=0)
        self.subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.subtitle_label.set_single_line_mode(True)
        self.subtitle_label.set_max_width_chars(1)
        self.subtitle_label.add_css_class("ac-tab-subtitle")
        text.append(self.title_label)
        text.append(self.subtitle_label)
        self.git_label: Gtk.Label = _git_label()
        text.append(self.git_label)
        self.append(text)
        self._setup_trailing()
        actions = Gio.SimpleActionGroup()
        rename = Gio.SimpleAction.new("rename", None)
        _ = rename.connect("activate", self._begin_rename)
        actions.add_action(rename)
        self.insert_action_group("tab-row", actions)
        context = Gtk.GestureClick()
        context.set_button(3)
        _ = context.connect("pressed", self._popup_menu)
        self.add_controller(context)
        self.context_keys: Gtk.EventControllerKey = Gtk.EventControllerKey()
        _ = self.context_keys.connect("key-pressed", self._context_key)
        self.add_controller(self.context_keys)

    def _setup_trailing(self) -> None:
        self.close_button: Gtk.Button = Gtk.Button(icon_name="window-close-symbolic")
        self.close_button.add_css_class("flat")
        self.close_button.add_css_class("ac-tab-close")
        self.close_button.set_valign(Gtk.Align.CENTER)
        self.close_button.set_tooltip_text(translate("tabs.close"))
        self.waiting_label: Gtk.Label = Gtk.Label()
        self.waiting_label.add_css_class("numeric")
        self.waiting_label.add_css_class("ac-tab-wait")
        self.waiting_label.set_halign(Gtk.Align.END)
        trailing = Gtk.Overlay()
        trailing.set_child(self.close_button)
        trailing.add_overlay(self.waiting_label)
        trailing.set_measure_overlay(self.waiting_label, True)  # noqa: FBT003 -- GTK child setter is positional
        self.waiting_label.set_can_target(False)
        self.append(trailing)
        self.menu_button: Gtk.MenuButton = Gtk.MenuButton()
        self.menu_button.set_direction(Gtk.ArrowType.NONE)
        self.menu_button.set_always_show_arrow(False)
        self.menu_button.set_focusable(False)
        self.menu_button.add_css_class("ac-tab-menu-anchor")
        self.menu_button.set_valign(Gtk.Align.CENTER)
        self.menu_button.set_tooltip_text(translate("tabs.menu"))
        self.menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("tabs.menu"))],
        )
        self.append(self.menu_button)

    def bind_item(self, item: TabItem) -> None:
        self.unbind_item()
        self._item = item
        self.tab_id = str(item.tab_id)
        self.close_button.set_action_target_value(GLib.Variant("s", self.tab_id))
        self.close_button.set_action_name("win.request-close-tab")
        menu = Gio.Menu()
        menu.append(translate("tabs.rename"), "tab-row.rename")
        review = Gio.MenuItem.new(review_text("review.open"), None)
        review.set_action_and_target_value("win.open-review", GLib.Variant("s", self.tab_id))
        menu.append_item(review)
        close = Gio.MenuItem.new(translate("tabs.close"), None)
        close.set_action_and_target_value("win.request-close-tab", GLib.Variant("s", self.tab_id))
        menu.append_item(close)
        self.menu_button.set_menu_model(menu)
        self._notify = item.connect("notify", self._item_changed)
        self._settings = Gtk.Settings.get_default()
        if self._settings is not None:
            self._settings_notify = self._settings.connect("notify::gtk-enable-animations", self._animations_changed)
        self._render()

    def unbind_item(self) -> None:
        self.menu_button.popdown()
        self.menu_button.set_menu_model(None)
        if self._item is not None and self._notify is not None:
            self._item.disconnect(self._notify)
        if self._settings is not None and self._settings_notify is not None:
            self._settings.disconnect(self._settings_notify)
        self._item = None
        self._notify = None
        self._settings = None
        self._settings_notify = None
        self.tab_id = None
        self.close_button.set_action_name(None)
        self.close_button.set_action_target_value(None)
        if self.title_label.get_editing():
            self.title_label.stop_editing(False)  # noqa: FBT003 -- cancel the recycled row's edit
        self.title_label.set_editable(False)

    def _begin_rename(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        if self._item is not None:
            self.title_label.set_editable(True)
            self.title_label.start_editing()

    def _popup_menu(self, _gesture: Gtk.GestureClick, _presses: int, _x: float, _y: float) -> None:
        if self._item is not None:
            self.menu_button.popup()

    def _context_key(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, state: Gdk.ModifierType,
    ) -> bool:
        context_key = keyval == Gdk.KEY_Menu or (
            keyval == Gdk.KEY_F10 and bool(state & Gdk.ModifierType.SHIFT_MASK)
        )
        if self._item is None or not context_key:
            return False
        self.menu_button.popup()
        return True

    def _item_changed(self, _item: TabItem, _property: object) -> None:
        self._render()

    def _animations_changed(self, _settings: Gtk.Settings, _property: object) -> None:
        self._render()

    def _render(self) -> None:
        item = self._item
        if item is None:
            return
        if not self.title_label.get_editing():
            self.title_label.set_text(str(item.title))
        subtitle = str(item.subtitle)
        detail = str(item.detail)
        self.subtitle_label.set_label(" · ".join(value for value in (subtitle, detail) if value))
        self.subtitle_label.set_tooltip_text(self.subtitle_label.get_label())
        git_line = str(item.git_line)
        self.git_label.set_label(git_line)
        self.git_label.set_tooltip_text(git_line or None)
        self.git_label.set_visible(bool(git_line))
        if item.git_warning:
            self.git_label.add_css_class("warning")
        else:
            self.git_label.remove_css_class("warning")
        minutes = int(str(item.waiting_minutes))
        self.waiting_label.set_visible(minutes >= 0)
        self.waiting_label.set_label(_wait_text(minutes) if minutes >= 0 else "")
        self.close_button.set_tooltip_text(translate("tabs.close"))
        self.status_label.set_label(str(item.status_symbol))
        status = translate(f"tabs.status.{item.status_code or 'unknown'}")
        if self._status_class is not None:
            self.status_label.remove_css_class(self._status_class)
        self._status_class = f"ac-status-{item.status_code or 'unknown'}"
        self.status_label.add_css_class(self._status_class)
        self.status_label.set_tooltip_text(status)
        self.status_label.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, status)],
        )
        self.status_spinner.set_tooltip_text(status)
        self.status_spinner.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, status)],
        )
        spinning = bool(item.spinning)
        self.status_spinner.set_visible(spinning)
        self.status_spinner.set_spinning(spinning)
        self.status_label.set_visible(not spinning)
        if bool(item.attention):
            self.status_label.add_css_class("ac-attention-ring")
            if self._settings is not None and bool(self._settings.props.gtk_enable_animations):
                self.status_label.add_css_class("ac-attention-animated")
            else:
                self.status_label.remove_css_class("ac-attention-animated")
        else:
            self.status_label.remove_css_class("ac-attention-ring")
            self.status_label.remove_css_class("ac-attention-animated")

    def _editing_changed(self, label: Gtk.EditableLabel, _property: object) -> None:
        item = self._item
        if item is None or self.tab_id is None or label.get_editing():
            return
        label.set_editable(False)
        title = label.get_text()
        if title == item.title:
            return
        if not title.strip() or len(title) > MAX_TAB_TITLE_LENGTH:
            self._render()
            return
        _ = spawn_task(self._rename(self.tab_id, title))

    async def _rename(self, tab_id: str, title: str) -> None:
        try:
            await self.tabs.rename(tab_id, title)
        except Exception:  # noqa: BLE001 -- restore the authoritative title after a failed request
            self._render()
