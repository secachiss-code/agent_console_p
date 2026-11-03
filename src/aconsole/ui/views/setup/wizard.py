"""Skippable onboarding with explicit preview and apply actions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AgentInfo, ProjectView  # noqa: E402
from aconsole.contracts.common import ProjectId, TabId  # noqa: E402
from aconsole.contracts.events import AgentsChanged, ProjectsChanged, UiEvent  # noqa: E402
from aconsole.contracts.settings import SettingsPatch  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.client.store import CoreStore  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel, SetupViewModel  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

_PREVIEW_STEP = 2
_DONE_STEP = 4

class SetupWizard(Adw.Dialog):
    def __init__(self, client: CoreClient, preferences: PreferencesViewModel, on_tab: Callable[[TabId], None]) -> None:
        super().__init__(title=translate("setup.title"), content_width=640, content_height=540)
        self.viewmodel: SetupViewModel = SetupViewModel(client)
        self._preferences: PreferencesViewModel = preferences
        self._on_tab: Callable[[TabId], None] = on_tab
        self._client: CoreClient = client
        snapshot = client.store.snapshot
        self._projects: tuple[ProjectView, ...] = snapshot.projects if snapshot is not None else ()
        self._agents: tuple[AgentInfo, ...] = tuple(
            agent for agent in snapshot.agents if agent.available and agent.supported
        ) if snapshot is not None else ()
        self.step: int = 0
        self.loading: bool = False
        self.worker: asyncio.Task[None] | None = None
        self._busy: bool = False
        self._active: bool = False
        self._generation: int = 0
        self._syncing_fixes: bool = False
        self._fix_ids: tuple[str, ...] = ()
        self._local_projects: dict[ProjectId, ProjectView] = {}
        self._catalog_dirty: bool = False
        self._page_copies: dict[str, Gtk.Label] = {}
        self._setup_view()
        _ = self.connect("closed", self._closed)
        client.store.subscribe(self._catalog_event)
        client.store.subscribe_snapshot(self._catalog_snapshot)

    def _setup_view(self) -> None:
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("ac-setup")
        self.stack: Gtk.Stack = Gtk.Stack(vexpand=True)
        # A step that slides in reads as a step; GTK skips it when the system turns animations off.
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(200)
        self.checks_box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        found = self._page("setup.found", "setup.checking")
        found.append(self.checks_box)
        _ = self.stack.add_named(found, "0")
        self._setup_project()
        self._setup_preview()
        self._setup_task()
        _ = self.stack.add_named(self._page("setup.done", "setup.done_copy"), "4")
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.stack)
        box.append(scroll)
        self.status_label: Gtk.Label = Gtk.Label(xalign=0, wrap=True)
        box.append(self.status_label)
        self._setup_navigation(box)
        toolbar.set_content(box)
        self.set_child(toolbar)

    def _page(self, title_key: str, copy_key: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        title = Gtk.Label(label=translate(title_key), xalign=0)
        title.add_css_class("title-2")
        box.append(title)
        copy = Gtk.Label(label=translate(copy_key), xalign=0, wrap=True)
        self._page_copies[title_key] = copy
        box.append(copy)
        return box

    def _setup_project(self) -> None:
        project = self._page("setup.project", "setup.project_copy")
        self.project_dropdown: Gtk.DropDown = Gtk.DropDown(
            model=Gtk.StringList.new([item.name for item in self._projects]),
        )
        self.project_dropdown.set_sensitive(bool(self._projects))
        project.append(self.project_dropdown)
        choose = Gtk.Button(label=translate("setup.folder"))
        _ = choose.connect("clicked", self._choose_folder)
        project.append(choose)
        _ = self.stack.add_named(project, "1")

    def _setup_preview(self) -> None:
        page = self._page("setup.changes", "setup.preview_copy")
        self.fix_dropdown: Gtk.DropDown = Gtk.DropDown(model=Gtk.StringList.new([]))
        _ = self.fix_dropdown.connect("notify::selected", self._fix_changed)
        page.append(self.fix_dropdown)
        preview = Gtk.Button(label=translate("setup.preview"))
        _ = preview.connect("clicked", self._preview_clicked)
        page.append(preview)
        self.preview_text: Gtk.TextView = Gtk.TextView(editable=False, monospace=True, vexpand=True)
        scroll = Gtk.ScrolledWindow(min_content_height=160)
        scroll.set_child(self.preview_text)
        page.append(scroll)
        self.apply_button: Gtk.Button = Gtk.Button(label=translate("setup.apply"), sensitive=False)
        _ = self.apply_button.connect("clicked", self._apply_clicked)
        page.append(self.apply_button)
        _ = self.stack.add_named(page, "2")

    def _setup_task(self) -> None:
        page = self._page("setup.task", "setup.task_copy")
        self.agent_dropdown: Gtk.DropDown = Gtk.DropDown(
            model=Gtk.StringList.new([agent.display_name for agent in self._agents]),
        )
        self.agent_dropdown.set_sensitive(bool(self._agents))
        page.append(self.agent_dropdown)
        self.first_task_button: Gtk.Button = Gtk.Button(label=translate("setup.open_task"))
        _ = self.first_task_button.connect("clicked", self._start_task)
        page.append(self.first_task_button)
        _ = self.stack.add_named(page, "3")

    def _setup_navigation(self, box: Gtk.Box) -> None:
        footer = Gtk.Box(spacing=8)
        self.back_button: Gtk.Button = Gtk.Button(label=translate("setup.back"))
        self.skip_button: Gtk.Button = Gtk.Button(label=translate("setup.skip"), hexpand=True, halign=Gtk.Align.START)
        self.next_button: Gtk.Button = Gtk.Button(label=translate("setup.next"))
        self.next_button.add_css_class("suggested-action")
        _ = self.back_button.connect("clicked", self._back_clicked)
        _ = self.skip_button.connect("clicked", self._skip_clicked)
        _ = self.next_button.connect("clicked", self._next_clicked)
        for button in (self.back_button, self.skip_button, self.next_button):
            footer.append(button)
        box.append(footer)

    def open(self, parent: Gtk.Widget) -> None:
        if self._active:
            self.present(parent)
            return
        self._active = True
        self._generation += 1
        self._refresh_catalogs()
        self.step = 0
        self.stack.set_visible_child_name("0")
        self.loading = True
        self._set_busy(busy=True)
        self.present(parent)
        self.worker = spawn_task(self._load(self._generation))

    async def _load(self, generation: int) -> None:
        try:
            await self.viewmodel.load_checks()
            if self._valid(generation):
                self._show_checks()
        except Exception:  # noqa: BLE001 -- allow skipping environment discovery after a read failure
            if self._valid(generation):
                self.status_label.set_label(translate("setup.error"))
        finally:
            if self._valid(generation):
                self.loading = False
                self._set_busy(busy=False)

    def _show_checks(self) -> None:
        self._refresh_catalogs()
        self._page_copies["setup.found"].set_label(translate("setup.checked"))
        while (child := self.checks_box.get_first_child()) is not None:
            self.checks_box.remove(child)
        snapshot = self._client.store.snapshot
        if snapshot is not None:
            for agent in snapshot.agents:
                key = "setup.available" if agent.available else "setup.unavailable"
                if agent.logged_in is False:
                    key = "setup.login"
                self.checks_box.append(Gtk.Label(label=f"{agent.display_name} · {translate(key)}", xalign=0))
        for check in self.viewmodel.checks:
            label = Gtk.Label(label=f"{'✓' if check.ok else '!'} {check.title}", xalign=0)
            label.set_tooltip_text(check.detail)
            self.checks_box.append(label)
        checks = tuple(check for check in self.viewmodel.checks if check.fix_id is not None)
        self._fix_ids = tuple(check.fix_id for check in checks if check.fix_id is not None)
        self._syncing_fixes = True
        self.fix_dropdown.set_model(Gtk.StringList.new([check.title for check in checks]))
        self._syncing_fixes = False

    def _valid(self, generation: int) -> bool:
        return self._active and generation == self._generation

    def _refresh_catalogs(self) -> None:
        self._catalog_dirty = False
        snapshot = self._client.store.snapshot
        if snapshot is None:
            return
        project_index, agent_index = self.project_dropdown.get_selected(), self.agent_dropdown.get_selected()
        project_id = self._projects[project_index].id if project_index < len(self._projects) else None
        agent_kind = self._agents[agent_index].kind if agent_index < len(self._agents) else None
        projects = {project.id: project for project in snapshot.projects}
        for key, value in tuple(self._local_projects.items()):
            if key in projects:
                del self._local_projects[key]
            else:
                projects[key] = value
        self._projects = tuple(projects.values())
        self._agents = tuple(agent for agent in snapshot.agents if agent.available and agent.supported)
        self.project_dropdown.set_model(Gtk.StringList.new([project.name for project in self._projects]))
        self.agent_dropdown.set_model(Gtk.StringList.new([agent.display_name for agent in self._agents]))
        self.project_dropdown.set_selected(next(
            (index for index, project in enumerate(self._projects) if project.id == project_id),
            0 if project_id is None and self._projects else Gtk.INVALID_LIST_POSITION,
        ))
        self.agent_dropdown.set_selected(next(
            (index for index, agent in enumerate(self._agents) if agent.kind == agent_kind),
            0 if agent_kind is None and self._agents else Gtk.INVALID_LIST_POSITION,
        ))
        self.project_dropdown.set_sensitive(bool(self._projects))
        self.agent_dropdown.set_sensitive(bool(self._agents))

    def _catalog_event(self, event: UiEvent, store: CoreStore) -> None:
        if isinstance(event, (AgentsChanged, ProjectsChanged)):
            self._catalog_snapshot(store)

    def _catalog_snapshot(self, _store: CoreStore) -> None:
        if self._active:
            self._catalog_dirty = True
            if not self._busy:
                self._set_busy(busy=False)

    def _set_busy(self, *, busy: bool) -> None:
        self._busy = busy
        if not busy and self._catalog_dirty:
            self._refresh_catalogs()
        self.next_button.set_sensitive(not busy)
        self.back_button.set_sensitive(not busy and self.step > 0)
        self.skip_button.set_sensitive(not busy)
        self.fix_dropdown.set_sensitive(not busy and bool(self._fix_ids))
        self.first_task_button.set_sensitive(
            not busy and self.project_dropdown.get_selected() < len(self._projects)
            and self.agent_dropdown.get_selected() < len(self._agents),
        )
        preview = self.viewmodel.preview
        self.apply_button.set_sensitive(not busy and preview is not None and preview.expires_at > datetime.now(UTC))

    def next_step(self) -> None:
        if self._busy:
            return
        if self.step == _DONE_STEP:
            self.skip()
            return
        self._navigate(self.step + 1)

    def previous_step(self) -> None:
        if not self._busy and self.step > 0:
            self._navigate(self.step - 1)

    def _navigate(self, step: int) -> None:
        self._generation += 1
        self.viewmodel.discard_preview()
        self._set_busy(busy=True)
        self.worker = spawn_task(self._change_step(step, self._generation))

    async def _change_step(self, step: int, generation: int) -> None:
        try:
            self._refresh_catalogs()
            project_index = self.project_dropdown.get_selected()
            if self.step == 1 and step > 1 and project_index < len(self._projects):
                await self.viewmodel.select_project(self._projects[project_index].id)
            if not self._valid(generation):
                return
            self.step = step
            self.stack.set_visible_child_name(str(step))
            self.next_button.set_label(translate("setup.finish" if step == _DONE_STEP else "setup.next"))
            self.status_label.set_label("")
            if step == _PREVIEW_STEP:
                await self._load_preview(generation)
        except Exception:  # noqa: BLE001 -- do not advance past a failed user-requested project operation
            if self._valid(generation):
                self.status_label.set_label(translate("setup.error"))
        finally:
            if self._valid(generation):
                self._set_busy(busy=False)

    async def _load_preview(self, generation: int) -> None:
        index = self.fix_dropdown.get_selected()
        if index >= len(self._fix_ids):
            self.preview_text.get_buffer().set_text(translate("setup.no_changes"))
            return
        await self.viewmodel.preview_fix(self._fix_ids[index])
        preview = self.viewmodel.preview
        if self._valid(generation) and preview is not None:
            self.preview_text.get_buffer().set_text("\n\n".join(
                value for value in (preview.summary, preview.diff, "\n".join(preview.commands)) if value
            ))
            if preview.expires_at <= datetime.now(UTC):
                self.status_label.set_label(translate("setup.expired"))

    def _fix_changed(self, _dropdown: Gtk.DropDown, _property: object) -> None:
        if not self._syncing_fixes and not self._busy and self.step == _PREVIEW_STEP:
            self._preview_clicked(self.apply_button)

    def _preview_clicked(self, _button: Gtk.Button) -> None:
        if not self._busy:
            self._navigate(_PREVIEW_STEP)

    def _apply_clicked(self, _button: Gtk.Button) -> None:
        if not self._busy:
            self._set_busy(busy=True)
            self.worker = spawn_task(self._apply(self._generation))

    async def _apply(self, generation: int) -> None:
        try:
            result = await self.viewmodel.apply_preview(datetime.now(UTC))
            await self.viewmodel.load_checks()
            if self._valid(generation):
                self._show_checks()
                self.status_label.set_label(
                    translate("setup.applied") if result.ok else (result.detail or result.title),
                )
        except Exception:  # noqa: BLE001 -- applying is never retried without another explicit action
            if self._valid(generation):
                self.status_label.set_label(translate("setup.error"))
        finally:
            if self._valid(generation):
                self._set_busy(busy=False)

    def skip(self) -> None:
        if not self._busy:
            self._set_busy(busy=True)
            self.worker = spawn_task(self._complete(self._generation))

    async def _complete(self, generation: int) -> None:
        try:
            _ = await self._preferences.update(SettingsPatch(setup_completed=True))
            if self._valid(generation):
                self.step = _DONE_STEP
                _ = self.close()
        except Exception:  # noqa: BLE001 -- failed persistence leaves an explicit retry rather than closing silently
            if self._valid(generation):
                self.status_label.set_label(translate("setup.error"))
        finally:
            if self._valid(generation):
                self._set_busy(busy=False)

    def _choose_folder(self, _button: Gtk.Button) -> None:
        parent = self.get_root()
        if isinstance(parent, Gtk.Window):
            dialog = Gtk.FileDialog(title=translate("setup.folder"))
            dialog.select_folder(parent, None, self._folder_selected)

    def _folder_selected(self, dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            file = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = file.get_path()
        if self._active and not self._busy and path is not None:
            self._set_busy(busy=True)
            self.worker = spawn_task(self._add_project(path, self._generation))

    async def _add_project(self, path: str, generation: int) -> None:
        try:
            project = await self.viewmodel.add_project(path)
            if self._valid(generation):
                self._local_projects[project.id] = project
                self._refresh_catalogs()
                self.project_dropdown.set_selected(next(
                    index for index, item in enumerate(self._projects) if item.id == project.id
                ))
        except Exception:  # noqa: BLE001 -- a rejected path does not start a task or alter the selected project
            if self._valid(generation):
                self.status_label.set_label(translate("setup.error"))
        finally:
            if self._valid(generation):
                self._set_busy(busy=False)

    def _start_task(self, _button: Gtk.Button) -> None:
        if not self._busy and self._projects and self._agents:
            self._set_busy(busy=True)
            self.worker = spawn_task(self._open_task(self._generation))

    async def _open_task(self, generation: int) -> None:
        try:
            project = self._projects[self.project_dropdown.get_selected()]
            agent = self._agents[self.agent_dropdown.get_selected()]
            tab = await self.viewmodel.start_first_task(project.id, agent.kind)
            if self._valid(generation):
                self._on_tab(tab.id)
                self.step = _DONE_STEP
                self.stack.set_visible_child_name("4")
                self.next_button.set_label(translate("setup.finish"))
        except Exception:  # noqa: BLE001 -- keep the optional first-task step available after a launch failure
            if self._valid(generation):
                self.status_label.set_label(translate("setup.error"))
        finally:
            if self._valid(generation):
                self._set_busy(busy=False)

    def _next_clicked(self, _button: Gtk.Button) -> None:
        self.next_step()

    def _back_clicked(self, _button: Gtk.Button) -> None:
        self.previous_step()

    def _skip_clicked(self, _button: Gtk.Button) -> None:
        self.skip()

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._active = False
        self._generation += 1
        self.viewmodel.discard_preview()
