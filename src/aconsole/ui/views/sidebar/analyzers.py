"""Manage project analyzers through the core's analyzer methods."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from aconsole.contracts.app import (  # noqa: E402
    AnalyzerEnableParams,
    AnalyzerItem,
    AnalyzerListParams,
    AnalyzerListResult,
    AnalyzerManageParams,
)
from aconsole.contracts.common import ProjectId, TabId  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.i18n import _ as translate  # noqa: E402

type AnalyzerAction = Literal["install", "update", "remove", "check"]


_ACTIONS: tuple[AnalyzerAction, ...] = ("install", "update", "remove", "check")
_ICONS: Mapping[AnalyzerAction, str] = {
    "install": "list-add-symbolic",
    "update": "software-update-available-symbolic",
    "remove": "user-trash-symbolic",
    "check": "view-refresh-symbolic",
}


class AnalyzerManagerDialog(Adw.Dialog):
    def __init__(self, client: CoreClient, project_id: ProjectId, tab_id: TabId | None) -> None:
        super().__init__(title=translate("analyzer.title"), content_width=640, content_height=360)
        self._client: CoreClient = client
        self._project_id: ProjectId = project_id
        self._tab_id: TabId | None = tab_id
        self._busy: bool = False
        self._items: dict[str, AnalyzerItem] = {}
        self._controls: dict[str, dict[AnalyzerAction, Gtk.Button]] = {}
        self._rows: list[Adw.ExpanderRow] = []

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=16,
            margin_end=16,
            margin_top=12,
            margin_bottom=16,
        )
        self.status: Gtk.Label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.status.set_visible(False)
        body.append(self.status)
        feature = Adw.PreferencesGroup(title="Python")
        self._enable_switch: Adw.SwitchRow = Adw.SwitchRow(title=translate("analyzer.enable"))
        self._enable_switch.set_sensitive(False)
        self._toggle_handler: int = self._enable_switch.connect("notify::active", self._toggle)
        self._preset_install: Gtk.Button = Gtk.Button(label=translate("analyzer.install_preset"))
        self._preset_install.set_sensitive(False)
        _ = self._preset_install.connect("clicked", self._install_preset)
        preset = Adw.ActionRow(title="Ruff + Pylint")
        preset.add_suffix(self._preset_install)
        self._preset_install.set_valign(Gtk.Align.CENTER)
        feature.add(preset)
        feature.add(self._enable_switch)
        body.append(feature)
        self.group: Adw.PreferencesGroup = Adw.PreferencesGroup()
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.group)
        body.append(scroll)
        toolbar.set_content(body)
        self.set_child(toolbar)
        _ = spawn_task(self.refresh())

    async def refresh(self) -> None:
        self.status.set_text(translate("analyzer.loading"))
        self.status.set_visible(True)
        self.status.remove_css_class("error")
        try:
            result = await self._client.request(
                "analyzer/list",
                AnalyzerListParams(project_id=self._project_id, tab_id=self._tab_id),
                AnalyzerListResult,
            )
        except Exception as exc:  # noqa: BLE001 -- show core refusals in the manager
            self.status.set_text(translate("analyzer.error").format(error=exc))
            self.status.add_css_class("error")
            return
        self._show_enabled(enabled=result.enabled)
        self._render(result.items)
        self._sync_controls()
        self.status.set_text(translate("analyzer.empty") if not result.items else "")
        self.status.set_visible(not result.items)

    def _show_enabled(self, *, enabled: bool) -> None:
        with self._enable_switch.handler_block(self._toggle_handler):
            self._enable_switch.set_active(enabled)
        self._enable_switch.set_sensitive(True)

    def _toggle(self, row: Adw.SwitchRow, _property: GObject.ParamSpec) -> None:
        row.set_sensitive(False)
        _ = spawn_task(self._persist_enabled(enabled=row.get_active()))

    async def _persist_enabled(self, *, enabled: bool) -> None:
        try:
            result = await self._client.request(
                "analyzer/setEnabled",
                AnalyzerEnableParams(project_id=self._project_id, tab_id=self._tab_id, enabled=enabled),
                AnalyzerListResult,
            )
        except Exception as exc:  # noqa: BLE001 -- re-read the persisted state after a failed request
            await self.refresh()
            self.status.set_text(translate("analyzer.error").format(error=exc))
            self.status.add_css_class("error")
            self.status.set_visible(True)
        else:
            self._show_enabled(enabled=result.enabled)

    def _render(self, items: tuple[AnalyzerItem, ...]) -> None:
        self._items = {item.id: item for item in items}
        self._controls.clear()
        for row in self._rows:
            self.group.remove(row)
        self._rows.clear()
        for item in items:
            row = Adw.ExpanderRow(title=item.name, subtitle=self._summary(item), use_markup=False)
            controls: dict[AnalyzerAction, Gtk.Button] = {}
            for action in _ACTIONS:
                button = Gtk.Button(icon_name=_ICONS[action], valign=Gtk.Align.CENTER)
                label = translate(f"analyzer.action.{action}")
                button.set_tooltip_text(label)
                button.update_property(
                    [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, label)]
                )
                button.set_sensitive(not self._busy and self._enabled(item, action))
                _ = button.connect("clicked", self._manage, item.id, action)
                row.add_suffix(button)
                controls[action] = button
            detail = Adw.ActionRow(
                title=self._version_text(item),
                subtitle=item.detail or "",
                use_markup=False,
            )
            row.add_row(detail)
            self.group.add(row)
            self._rows.append(row)
            self._controls[item.id] = controls

    @staticmethod
    def _summary(item: AnalyzerItem) -> str:
        parts = [translate(f"analyzer.state.{item.state}")]
        if item.version:
            parts.append(translate("analyzer.available_version").format(version=item.version))
        return " · ".join(parts)

    @staticmethod
    def _version_text(item: AnalyzerItem) -> str:
        installed = item.installed_version or translate("analyzer.not_installed")
        return translate("analyzer.installed_version").format(version=installed)

    @staticmethod
    def _enabled(item: AnalyzerItem, action: AnalyzerAction) -> bool:
        installed = bool(item.installed_version)
        if action == "install":
            return not installed and item.state != "unsupported"
        if action == "update":
            return item.state == "update_available"
        if action == "remove":
            return installed
        return installed

    def _install_preset(self, _button: Gtk.Button) -> None:
        pending = tuple(key for key, item in self._items.items() if self._enabled(item, "install"))
        self._start(pending, "install")

    def _manage(self, _button: Gtk.Button, analyzer_id: str, action: AnalyzerAction) -> None:
        self._start((analyzer_id,), action)

    def _start(self, analyzer_ids: tuple[str, ...], action: AnalyzerAction) -> None:
        if self._busy or not analyzer_ids:
            return
        self._busy = True
        self._sync_controls()
        self.status.set_text(translate("analyzer.working").format(action=translate(f"analyzer.action.{action}")))
        self.status.set_visible(True)
        self.status.remove_css_class("error")
        _ = spawn_task(self._perform(analyzer_ids, action))

    async def _perform(self, analyzer_ids: tuple[str, ...], action: AnalyzerAction) -> None:
        try:
            for analyzer_id in analyzer_ids:
                item = await self._client.request(
                    "analyzer/manage",
                    AnalyzerManageParams(
                        project_id=self._project_id,
                        tab_id=self._tab_id,
                        id=analyzer_id,
                        action=action,
                    ),
                    AnalyzerItem,
                )
                self._items[analyzer_id] = item
                self._render(tuple(self._items.values()))
                if item.state == "error":
                    self.status.set_text(item.detail or translate("analyzer.action_failed").format(name=item.name))
                    self.status.add_css_class("error")
                    break
                self.status.set_text(translate("analyzer.updated").format(name=item.name))
        except Exception as exc:  # noqa: BLE001 -- keep the dialog available after a refused operation
            self.status.set_text(translate("analyzer.error").format(error=exc))
            self.status.add_css_class("error")
        finally:
            self._busy = False
            self._sync_controls()

    def _sync_controls(self) -> None:
        self._preset_install.set_sensitive(
            not self._busy and any(self._enabled(item, "install") for item in self._items.values()),
        )
        for analyzer_id, item in self._items.items():
            for action, control in self._controls.get(analyzer_id, {}).items():
                control.set_sensitive(not self._busy and self._enabled(item, action))
