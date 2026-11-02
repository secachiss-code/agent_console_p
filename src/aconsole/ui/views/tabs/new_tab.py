"""Choose a project and an agent, then open a tab in the core."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AgentInfo, ProjectView  # noqa: E402
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as translate  # noqa: E402


class NewTabDialog(Adw.Dialog):
    def __init__(
        self, tabs: TabsViewModel, on_opened: Callable[[TabId], None], on_error: Callable[[str], None],
    ) -> None:
        super().__init__(title=translate("tabs.new"), content_width=420)
        self._tabs: TabsViewModel = tabs
        self._on_opened: Callable[[TabId], None] = on_opened
        self._on_error: Callable[[str], None] = on_error
        self._projects: tuple[ProjectView, ...] = ()
        self._agents: tuple[AgentInfo, ...] = ()
        self.project_row: Adw.ComboRow = Adw.ComboRow(title=translate("tabs.new.project"))
        self.agent_row: Adw.ComboRow = Adw.ComboRow(title=translate("tabs.new.agent"))
        self.hint: Gtk.Label = Gtk.Label(xalign=0, wrap=True)
        self.hint.add_css_class("ac-summary-detail")
        self.add_project_button: Gtk.Button = Gtk.Button(
            label=translate("tabs.new.add_project"), halign=Gtk.Align.START,
        )
        self.add_project_button.add_css_class("flat")
        _ = self.add_project_button.connect("clicked", self._choose_folder)
        group = Adw.PreferencesGroup()
        group.add(self.project_row)
        group.add(self.agent_row)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("ac-new-tab-body")
        body.append(group)
        body.append(self.add_project_button)
        body.append(self.hint)
        self.open_button: Gtk.Button = Gtk.Button(label=translate("tabs.new.open"))
        self.open_button.add_css_class("suggested-action")
        _ = self.open_button.connect("clicked", self._open)
        header = Adw.HeaderBar()
        header.pack_end(self.open_button)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(body)
        self.set_child(toolbar)
        self.reload()

    def reload(self, selected: ProjectView | None = None) -> None:
        snapshot = self._tabs.client.store.snapshot
        self._projects = snapshot.projects if snapshot is not None else ()
        self._agents = tuple(agent for agent in snapshot.agents if agent.available) if snapshot is not None else ()
        if selected is not None and selected.id not in {project.id for project in self._projects}:
            self._projects = (*self._projects, selected)
        active = selected.id if selected is not None else (snapshot.active_project if snapshot is not None else None)
        self.project_row.set_model(Gtk.StringList.new([project.name for project in self._projects]))
        for index, project in enumerate(self._projects):
            if project.id == active:
                self.project_row.set_selected(index)
        self.agent_row.set_model(Gtk.StringList.new([agent.display_name for agent in self._agents]))
        hint = ""
        if not self._projects:
            hint = translate("tabs.new.no_projects")
        elif not self._agents:
            hint = translate("tabs.new.no_agents")
        self.hint.set_label(hint)
        self.hint.set_visible(bool(hint))
        self.open_button.set_sensitive(bool(self._projects and self._agents))

    def _choose_folder(self, _button: Gtk.Button) -> None:
        root = self.get_root()
        Gtk.FileDialog().select_folder(root if isinstance(root, Gtk.Window) else None, None, self._folder_chosen)

    def _folder_chosen(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:  # dismissed
            return
        path = folder.get_path()
        if path is not None:
            _ = spawn_task(self._add_project(path))

    async def _add_project(self, path: str) -> None:
        try:
            project = await self._tabs.add_project(path)
        except Exception as error:  # noqa: BLE001 -- not a repository: say so and keep the dialog
            self._on_error(translate("tabs.new.project_error").format(error=error))
            return
        self.reload(project)

    def _open(self, _button: Gtk.Button) -> None:
        project = self._projects[self.project_row.get_selected()]
        agent = self._agents[self.agent_row.get_selected()]
        self.open_button.set_sensitive(False)
        _ = spawn_task(self._open_tab(project, agent))

    async def _open_tab(self, project: ProjectView, agent: AgentInfo) -> None:
        try:
            tab = await self._tabs.open(project.id, agent.kind)
        except Exception as error:  # noqa: BLE001 -- the core's refusal is shown, the dialog stays for another choice
            self._on_error(translate("tabs.new.error").format(error=error))
            self.open_button.set_sensitive(True)
            return
        _ = self.close()
        self._on_opened(tab.id)
