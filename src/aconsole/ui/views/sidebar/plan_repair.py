"""Explicit repair of legacy frozen profiles through the normal plan API."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import PlanSaveParams, PlanView  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

_LIMIT = 256 * 1024


class PlanRepairDialog(Adw.Dialog):
    def __init__(self, client: CoreClient, plan: PlanView, on_saved: Callable[[PlanView], None]) -> None:
        super().__init__(title=translate("plan.repair"), content_width=600, content_height=480)
        self._client: CoreClient = client
        self._plan: PlanView = plan
        self._on_saved: Callable[[PlanView], None] = on_saved
        self._busy: bool = False
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=16,
            margin_end=16,
            margin_top=12,
            margin_bottom=16,
        )
        box.append(Gtk.Label(label=translate("plan.repair_help"), xalign=0, wrap=True))
        self.load_button: Gtk.Button = Gtk.Button(label=translate("plan.repair_load"), halign=Gtk.Align.START)
        _ = self.load_button.connect("clicked", self._choose_file)
        box.append(self.load_button)
        self.editor: Gtk.TextView = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.CHAR)
        self.editor.set_tooltip_text(translate("plan.repair_json"))
        scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=160)
        scroll.set_child(self.editor)
        box.append(scroll)
        self.error_label: Gtk.Label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.error_label.add_css_class("error")
        self.error_label.set_visible(False)
        box.append(self.error_label)
        self.apply_button: Gtk.Button = Gtk.Button(label=translate("plan.repair_apply"))
        self.apply_button.add_css_class("suggested-action")
        _ = self.apply_button.connect("clicked", self._apply)
        box.append(self.apply_button)
        toolbar.set_content(box)
        self.set_child(toolbar)

    def _error(self, error: str) -> None:
        self.error_label.set_text(error)
        self.error_label.set_visible(True)

    def _set_busy(self, *, busy: bool) -> None:
        self._busy = busy
        self.set_can_close(not busy)
        self.apply_button.set_sensitive(not busy)
        self.load_button.set_sensitive(not busy)
        self.editor.set_editable(not busy)
        self.error_label.set_visible(False)

    def _choose_file(self, _button: Gtk.Button) -> None:
        if self._busy:
            return
        root = self.get_root()
        Gtk.FileDialog(title=translate("plan.repair_load")).open(
            root if isinstance(root, Gtk.Window) else None,
            None,
            self._file_chosen,
        )

    def _file_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            chosen = dialog.open_finish(result)
        except GLib.Error:
            return
        path = chosen.get_path()
        if path is not None:
            self._set_busy(busy=True)
            _ = spawn_task(self._load(Path(path)))

    async def _load(self, path: Path) -> None:
        error = None
        try:
            raw = await asyncio.to_thread(_read_file, path)
            self.editor.get_buffer().set_text(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            error = str(exc)
        finally:
            self._set_busy(busy=False)
        if error is not None:
            self._error(error)

    def _apply(self, _button: Gtk.Button) -> None:
        if self._busy:
            return
        buffer = self.editor.get_buffer()
        raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), include_hidden_chars=True).encode("utf-8")
        if len(raw) > _LIMIT:
            self._error(translate("plan.repair_size"))
            return
        try:
            profiles = msgspec.json.decode(raw, type=dict[str, dict[str, msgspec.Raw]])
        except msgspec.DecodeError as exc:
            self._error(str(exc))
            return
        if not profiles:
            self._error(translate("plan.repair_empty"))
            return
        params = PlanSaveParams(
            plan_id=self._plan.id,
            project_id=self._plan.project_id,
            title=self._plan.title,
            goal=self._plan.goal,
            repair_gate_profiles={key: msgspec.Raw(msgspec.json.encode(profile)) for key, profile in profiles.items()},
        )
        self._set_busy(busy=True)
        _ = spawn_task(self._save(params))

    async def _save(self, params: PlanSaveParams) -> None:
        try:
            result = await self._client.request("plan/save", params, PlanView)
        except Exception as exc:  # noqa: BLE001 -- keep the entered profiles when the core refuses a repair
            self._set_busy(busy=False)
            self._error(str(exc))
        else:
            self._set_busy(busy=False)
            self._on_saved(result)
            _ = self.close()


def _read_file(path: Path) -> bytes:
    with path.open("rb") as source:
        raw = source.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError(translate("plan.repair_size"))
    return raw
