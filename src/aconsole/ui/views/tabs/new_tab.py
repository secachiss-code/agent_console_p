"""Choose a project and an agent, then open a tab in the core."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AgentInfo, ProjectView  # noqa: E402
from aconsole.contracts.common import TabId, WorkspaceMode  # noqa: E402
from aconsole.contracts.work_sessions import WorkSessionListParams, WorkSessionListResult, WorkSessionView  # noqa: E402 -- GTK imports or an operation error shown to the user
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as translate  # noqa: E402

_WORKSPACES = (WorkspaceMode.NEW_WORKTREE, WorkspaceMode.MAIN_CHECKOUT, WorkspaceMode.EXISTING_WORKTREE)


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
        self._opening: bool = False
        self._sessions: tuple[WorkSessionView, ...] = ()
        self._session_project: str | None = None
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
        self.session_row: Adw.ComboRow = Adw.ComboRow(
            title=translate("tabs.new.work_session"), model=Gtk.StringList.new([translate("tabs.new.task")]),
        )
        group.add(self.session_row)
        self.task_title: Adw.EntryRow = Adw.EntryRow(title=translate("tabs.new.task_title"))
        self.task_title.set_text(translate("tabs.new.task"))
        group.add(self.task_title)
        self.isolated_row: Adw.SwitchRow = Adw.SwitchRow(
            title=translate("tabs.new.isolated"), subtitle=translate("tabs.new.isolated_hint"), visible=False,
        )
        group.add(self.isolated_row)
        self._build_workspace(group)
        _ = self.session_row.connect("notify::selected", self._workspace_changed)
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

    def _build_workspace(self, group: Adw.PreferencesGroup) -> None:
        self.workspace_row: Adw.ComboRow = Adw.ComboRow(
            title=translate("tabs.new.workspace"),
            model=Gtk.StringList.new([translate(f"tabs.new.{mode.value}") for mode in _WORKSPACES]),
        )
        self.existing_path: Adw.EntryRow = Adw.EntryRow(title=translate("tabs.new.path"), visible=False)
        group.add(self.workspace_row)
        group.add(self.existing_path)
        self.location: Gtk.Label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        group.add(self.location)
        _ = self.workspace_row.connect("notify::selected", self._workspace_changed)
        _ = self.project_row.connect("notify::selected", self._workspace_changed)
        _ = self.existing_path.connect("changed", self._workspace_changed)

    def _workspace_changed(self, _source: object, _property: object = None) -> None:
        session_index = self.session_row.get_selected()
        has_session = 0 < session_index <= len(self._sessions)
        self.workspace_row.set_visible(not has_session)
        self.task_title.set_visible(not has_session)
        self.isolated_row.set_visible(has_session)
        mode = _WORKSPACES[min(self.workspace_row.get_selected(), len(_WORKSPACES) - 1)]
        existing = mode is WorkspaceMode.EXISTING_WORKTREE
        self.existing_path.set_visible(existing and not has_session)
        index = self.project_row.get_selected()
        project = self._projects[index] if index < len(self._projects) else None
        if project is not None and self._session_project != str(project.id):
            self._session_project = str(project.id)
            self._sessions = ()
            self.session_row.set_model(Gtk.StringList.new([translate("tabs.new.task")]))
            _ = spawn_task(self._load_sessions(project))
        text = ""
        if project is not None:
            text = f"{project.root}\n{project.default_branch}"
            if mode is WorkspaceMode.MAIN_CHECKOUT:
                text += "\n" + translate("tabs.new.main_warning")
        self.location.set_label(text)
        if has_session:
            text = self._sessions[session_index - 1].worktree
            self.location.set_label(text)
        has_path = has_session or not existing or bool(self.existing_path.get_text().strip())
        self.open_button.set_sensitive(bool(project and self._agents and has_path) and not self._opening)

    async def _load_sessions(self, project: ProjectView) -> None:
        try:
            result = await self._tabs.client.request(
                "workSession/list", WorkSessionListParams(project_id=project.id), WorkSessionListResult,
            )
        except Exception:  # noqa: BLE001 -- GTK imports or an operation error shown to the user
            return
        if self._session_project != str(project.id):
            return
        self._sessions = result.sessions
        self.session_row.set_model(Gtk.StringList.new([
            translate("tabs.new.task"), *(row.title for row in result.sessions),
        ]))

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
        self._workspace_changed(self.workspace_row)

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
        if not self.open_button.get_sensitive():
            return
        project = self._projects[self.project_row.get_selected()]
        agent = self._agents[self.agent_row.get_selected()]
        mode = _WORKSPACES[min(self.workspace_row.get_selected(), len(_WORKSPACES) - 1)]
        path = self.existing_path.get_text().strip() if mode is WorkspaceMode.EXISTING_WORKTREE else None
        self._opening = True
        self.open_button.set_sensitive(False)
        _ = spawn_task(self._open_tab(project, agent, mode, path))

    async def _open_tab(self, project: ProjectView, agent: AgentInfo, mode: WorkspaceMode, path: str | None) -> None:
        try:
            index = self.session_row.get_selected()
            session = self._sessions[index - 1] if 0 < index <= len(self._sessions) else None
            tab = await self._tabs.open(
                project.id, agent.kind, workspace=mode, existing_worktree=path,
                work_session_id=None if session is None else session.id,
                isolated=session is not None and self.isolated_row.get_active(),
                title=self.task_title.get_text().strip() or None if session is None else None,
            )
        except Exception as error:  # noqa: BLE001 -- not a repository: say so and keep the dialog
            self._on_error(translate("tabs.new.error").format(error=error))
            self._opening = False
            self._workspace_changed(self.workspace_row)
            return
        _ = self.close()
        self._on_opened(tab.id)
