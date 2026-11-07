"""The project switcher and the session search above the tab list."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from aconsole.contracts.app import ProjectView
    from aconsole.contracts.events import UiEvent
    from aconsole.ui.client.store import CoreStore
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
    from aconsole.ui.views.tabs.sidebar import TabSidebar


class ProjectBar(Gtk.Box):
    def __init__(self, tabs: TabsViewModel, sidebar: TabSidebar) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("ac-project-bar")
        self._tabs: TabsViewModel = tabs
        self._sidebar: TabSidebar = sidebar
        self._projects: tuple[ProjectView, ...] = ()
        self._syncing: bool = False
        self._removing: bool = False
        self.projects: Gtk.DropDown = Gtk.DropDown()
        self.projects.set_tooltip_text(translate("tabs.project"))
        self.projects.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("tabs.project"))],
        )
        _ = self.projects.connect("notify::selected", self._project_chosen)
        self.search: Gtk.SearchEntry = Gtk.SearchEntry(placeholder_text=translate("tabs.search"))
        _ = self.search.connect("search-changed", self._search_changed)
        project_row = Gtk.Box(spacing=6)
        self.projects.set_hexpand(True)
        project_row.append(self.projects)
        self.remove_button: Gtk.Button = Gtk.Button(
            icon_name="list-remove-symbolic", tooltip_text=translate("tabs.project.remove"),
        )
        _ = self.remove_button.connect("clicked", self._confirm_remove)
        project_row.append(self.remove_button)
        self.append(project_row)
        self.error_label: Gtk.Label = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.append(self.error_label)
        self.append(self.search)
        store = tabs.client.store
        store.subscribe(self._on_event)
        store.subscribe_snapshot(self._on_snapshot)
        self._on_snapshot(store)

    def _on_event(self, _event: UiEvent, store: CoreStore) -> None:
        self._on_snapshot(store)

    def _on_snapshot(self, store: CoreStore) -> None:
        snapshot = store.snapshot
        projects = snapshot.projects if snapshot is not None else ()
        active = snapshot.active_project if snapshot is not None else None
        self._syncing = True
        if projects != self._projects:
            self._projects = projects
            self.projects.set_model(Gtk.StringList.new([project.name for project in projects]))
        self.projects.set_visible(bool(projects))
        for index, project in enumerate(projects):
            if project.id == active:
                self.projects.set_selected(index)
        self._syncing = False
        self._apply_filter()
        self._update_remove()

    def _update_remove(self) -> None:
        project = self._selected()
        snapshot = self._tabs.client.store.snapshot
        has_tabs = project is not None and snapshot is not None and any(
            tab.project_id == project.id for tab in snapshot.tabs
        )
        self.remove_button.set_sensitive(project is not None and not has_tabs and not self._removing)
        self.remove_button.set_tooltip_text(translate(
            "tabs.project.close_tabs" if has_tabs else "tabs.project.remove",
        ))

    def _confirm_remove(self, _button: Gtk.Button) -> None:
        project = self._selected()
        if project is None or not self.remove_button.get_sensitive():
            return
        dialog = Adw.AlertDialog(
            heading=translate("tabs.project.remove_heading").format(name=project.name),
            body=translate("tabs.project.remove_body"),
        )
        dialog.add_response("cancel", translate("tabs.close_cancel"))
        dialog.add_response("remove", translate("tabs.project.remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        _ = dialog.connect("response", self._remove_response, project)
        dialog.present(self)

    def _remove_response(self, _dialog: Adw.AlertDialog, response: str, project: ProjectView) -> None:
        if response == "remove" and not self._removing:
            self._removing = True
            self._update_remove()
            _ = spawn_task(self._remove_project(project))

    async def _remove_project(self, project: ProjectView) -> None:
        self.error_label.set_visible(False)
        try:
            await self._tabs.remove_project(project.id)
        except Exception as error:  # noqa: BLE001 -- keep the selected project and explain the refusal
            self.error_label.set_label(translate("tabs.project.remove_error").format(error=error))
            self.error_label.set_visible(True)
        finally:
            self._removing = False
            self._update_remove()

    def _selected(self) -> ProjectView | None:
        index = self.projects.get_selected()
        return self._projects[index] if index < len(self._projects) else None

    def _apply_filter(self) -> None:
        project = self._selected()
        self._sidebar.set_project(str(project.id) if project is not None else None)

    def _project_chosen(self, _dropdown: Gtk.DropDown, _property: object) -> None:
        if self._syncing:
            return
        self._apply_filter()
        self._update_remove()
        project = self._selected()
        if project is not None:
            _ = spawn_task(self._tabs.set_active_project(project.id))

    def _search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._sidebar.set_query(entry.get_text())

    def show_project(self, project_id: str) -> None:
        """Make a tab of ``project_id`` visible: a tab opened in another project must not vanish."""
        for index, project in enumerate(self._projects):
            if str(project.id) == project_id:
                self.projects.set_selected(index)
