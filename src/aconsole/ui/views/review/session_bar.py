"""Task context, handoffs, and immutable worker results on the Git page."""

from __future__ import annotations

from collections.abc import Callable, Coroutine

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402 -- GTK imports or an operation error shown to the user

from aconsole.contracts.app import EmptyResult, TabView  # noqa: E402 -- GTK imports or an operation error shown to the user
from aconsole.contracts.common import TabId  # noqa: E402 -- GTK imports or an operation error shown to the user
from aconsole.contracts.git import DiffSource, GitOperationResult, MergePreviewResult  # noqa: E402 -- GTK imports or an operation error shown to the user
from aconsole.contracts.work_sessions import (  # noqa: E402 -- GTK imports or an operation error shown to the user
    WorkSessionCleanupWorkerParams,
    WorkSessionHandoffParams,
    WorkSessionHistoryResult,
    WorkSessionIntegrateParams,
    WorkSessionIntegratePreviewParams,
    WorkSessionListParams,
    WorkSessionListResult,
    WorkSessionParams,
    WorkSessionRenameParams,
    WorkSessionRestoreParams,
    WorkSessionRestorePreview,
    WorkSessionRestorePreviewParams,
    WorkSessionResultParams,
    WorkSessionSnapshotView,
    WorkSessionView,
)
from aconsole.ui.bootstrap import spawn_task  # noqa: E402 -- GTK imports or an operation error shown to the user
from aconsole.ui.viewmodels.git_vm import GitViewModel  # noqa: E402 -- GTK imports or an operation error shown to the user
from aconsole.ui.views.review.messages import _ as translate  # noqa: E402 -- GTK imports or an operation error shown to the user


def _member_label(tab: TabView) -> str:
    mode = translate("git.session_isolated" if tab.isolated else "git.session_shared")
    return f"{tab.title} · {tab.agent.value} · {mode} · {tab.status.value}"


class SessionBar(Gtk.Box):
    def __init__(
        self,
        vm: GitViewModel,
        select: Callable[[], None],
        show_diff: Callable[[DiffSource], None],
        toast: Callable[[str], None],
        resume_tab: Callable[[TabId], None] | None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self._vm: GitViewModel = vm
        self._select: Callable[[], None] = select
        self._show_diff: Callable[[DiffSource], None] = show_diff
        self._toast: Callable[[str], None] = toast
        self._resume_tab: Callable[[TabId], None] | None = resume_tab
        self._sessions: tuple[WorkSessionView, ...] = ()
        self._members: tuple[TabView, ...] = ()
        self._history: tuple[WorkSessionSnapshotView, ...] = ()
        self._syncing: bool = False
        self._busy: bool = False
        self._generation: int = 0
        self.dialog: Adw.AlertDialog | None = None
        self.choice: Gtk.DropDown = Gtk.DropDown.new_from_strings([translate("git.session_project")])
        self.choice.set_hexpand(True)
        _ = self.choice.connect("notify::selected", self._changed)
        self.append(self.choice)
        self.detail: Gtk.Label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.detail.add_css_class("dim-label")
        self.append(self.detail)
        self.members: Gtk.DropDown = Gtk.DropDown.new_from_strings([])
        self.append(self.members)
        self.history: Gtk.DropDown = Gtk.DropDown.new_from_strings([])
        _ = self.history.connect("notify::selected", self._snapshot_selected)
        self.append(self.history)
        actions = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6)
        self.buttons: dict[str, Gtk.Button] = {}
        for key in ("rename", "handoff", "result", "integrate", "restore", "cleanup_worker", "archive"):
            button = Gtk.Button(label=translate(f"git.session_{key}"))
            _ = button.connect("clicked", self._clicked, key)
            actions.append(button)
            self.buttons[key] = button
        self.append(actions)
        _ = self.members.connect("notify::selected", self._selection_changed)
        self._actions: Gtk.FlowBox = actions

    async def refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        try:
            result = await self._vm.client.request(
                "workSession/list",
                WorkSessionListParams(
                    project_id=self._vm.project_id,
                    include_archived=True,
                ),
                WorkSessionListResult,
            )
            selected = self._vm.work_session_id
            history = (
                None
                if selected is None
                else await self._vm.client.request(
                    "workSession/history",
                    WorkSessionParams(project_id=self._vm.project_id, work_session_id=selected),
                    WorkSessionHistoryResult,
                )
            )
        except Exception:  # noqa: BLE001 -- GTK imports or an operation error shown to the user
            return
        if generation != self._generation:
            return
        self._syncing = True
        self._sessions = result.sessions
        self.set_visible(bool(self._sessions))
        self.choice.set_model(
            Gtk.StringList.new(
                [
                    translate("git.session_project"),
                    *(f"{row.title} ({translate(f'git.session_{row.status}')})" for row in self._sessions),
                ]
            )
        )
        index = next((at + 1 for at, row in enumerate(self._sessions) if row.id == selected), 0)
        self.choice.set_selected(index)
        record = None if index == 0 else self._sessions[index - 1]
        self.detail.set_text(
            "" if record is None else f"{record.worktree}\n{record.branch or ''} → {record.base_branch or ''}"
            + (f"\n{translate('git.session_removed')}" if record.workspace_removed else "")
        )
        snapshot = self._vm.client.store.snapshot
        previous_member = self._member()
        member_id = None if previous_member is None else previous_member.id
        self._members = tuple(
            tab for tab in (snapshot.tabs if snapshot is not None else ()) if tab.work_session_id == selected
        )
        self.members.set_model(
            Gtk.StringList.new(
                [_member_label(tab) for tab in self._members]
            )
        )
        self.members.set_selected(next((at for at, tab in enumerate(self._members) if tab.id == member_id), 0))
        old = self._snapshot()
        self._history = () if history is None else tuple(row for row in history.snapshots if row.oid is not None)
        self.history.set_model(
            Gtk.StringList.new([f"#{row.sequence} · {row.kind} · {row.label}" for row in self._history])
        )
        self.history.set_selected(
            next(
                (at for at, row in enumerate(self._history) if old is not None and old.sequence == row.sequence),
                max(0, len(self._history) - 1),
            )
        )
        self.members.set_visible(record is not None and bool(self._members))
        self.history.set_visible(record is not None and bool(self._history))
        self._actions.set_visible(record is not None)
        self._syncing = False
        self._update_buttons()

    def _member(self) -> TabView | None:
        at = self.members.get_selected()
        return self._members[at] if at < len(self._members) else None

    def _snapshot(self) -> WorkSessionSnapshotView | None:
        at = self.history.get_selected()
        return self._history[at] if at < len(self._history) else None

    def _selection_changed(self, _choice: Gtk.DropDown, _prop: GObject.ParamSpec) -> None:
        if not self._syncing:
            self._update_buttons()

    def _update_buttons(self) -> None:
        record = next((row for row in self._sessions if row.id == self._vm.work_session_id), None)
        active = record is not None and record.status == "active"
        member, point = self._member(), self._snapshot()
        self._vm.actor_tab_id = None if member is None else member.id
        available = {
            "rename": record is not None,
            "archive": record is not None and not record.workspace_removed,
            "handoff": active and member is not None,
            "result": active and member is not None and member.isolated,
            "restore": active and point is not None and record is not None and point.worktree == record.worktree,
            "integrate": active and point is not None and point.kind == "result",
            "cleanup_worker": record is not None and point is not None and point.kind == "result",
        }
        for key, button in self.buttons.items():
            button.set_sensitive(available[key] and not self._busy)
        self.buttons["archive"].set_label(
            translate(
                "git.session_resume" if record is not None and record.status == "archived" else "git.session_archive",
            )
        )

    def _changed(self, choice: Gtk.DropDown, _prop: GObject.ParamSpec) -> None:
        if self._syncing:
            return
        index = choice.get_selected()
        self._vm.work_session_id = self._sessions[index - 1].id if 0 < index <= len(self._sessions) else None
        self._select()

    def _snapshot_selected(self, _choice: Gtk.DropDown, _prop: GObject.ParamSpec) -> None:
        if self._syncing:
            return
        point = self._snapshot()
        self._update_buttons()
        if point is not None and point.oid is not None:
            self._show_diff(
                DiffSource(kind="between", oid=point.oid, against=point.previous_oid)
                if point.previous_oid
                else DiffSource(kind="commit", oid=point.oid)
            )

    def _clicked(self, _button: Gtk.Button, action: str) -> None:
        self._start(self._action(action))

    def _start(self, work: Coroutine[object, object, None]) -> None:
        _ = spawn_task(self._guard(work))

    async def _guard(self, work: Coroutine[object, object, None]) -> None:
        self._busy = True
        self._update_buttons()
        try:
            await work
        except Exception as exc:  # noqa: BLE001 -- GTK imports or an operation error shown to the user
            self._toast(str(exc))
        finally:
            self._busy = False
            self._select()

    def _ask(self, body: str, confirm: Callable[[], None], entry: Gtk.Entry | None = None) -> None:
        dialog = Adw.AlertDialog(heading=translate("git.session_confirm"), body=body)
        dialog.add_response("cancel", translate("git.cancel"))
        dialog.add_response("confirm", translate("git.session_confirm"))
        dialog.set_close_response("cancel")
        if entry is not None:
            dialog.set_extra_child(entry)

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "confirm":
                confirm()

        _ = dialog.connect("response", answered)
        self.dialog = dialog
        dialog.present(self)

    async def _action(self, action: str) -> None:
        session_id = self._vm.work_session_id
        if session_id is None:
            return
        params = WorkSessionParams(project_id=self._vm.project_id, work_session_id=session_id)
        member, point = self._member(), self._snapshot()
        if action == "rename":
            entry = Gtk.Entry()
            self._ask(
                translate("git.session_rename"), lambda: self._start(self._rename(params, entry.get_text())), entry
            )
        elif action == "archive":
            record = next(row for row in self._sessions if row.id == session_id)
            method = "workSession/resume" if record.status == "archived" else "workSession/close"
            self._ask(
                translate("git.session_reopen_hint" if record.status == "archived" else "git.session_archive_hint"),
                lambda: self._start(self._simple(method, params, WorkSessionView)),
            )
        elif action == "handoff" and member is not None:
            self._ask(translate("git.session_handoff_hint"), lambda: self._start(self._handoff(params, member.id)))
        elif action == "result" and member is not None:
            self._ask(
                translate("git.session_result_hint"),
                lambda: self._start(
                    self._simple(
                        "workSession/result",
                        WorkSessionResultParams(
                            project_id=params.project_id, work_session_id=session_id, tab_id=member.id
                        ),
                        WorkSessionHistoryResult,
                    )
                ),
            )
        elif action == "restore" and point is not None:
            preview = await self._vm.client.request(
                "workSession/restorePreview",
                WorkSessionRestorePreviewParams(
                    project_id=params.project_id,
                    work_session_id=session_id,
                    sequence=point.sequence,
                ),
                WorkSessionRestorePreview,
            )
            self._ask(
                translate("git.session_restore_hint"),
                lambda: self._start(
                    self._simple(
                        "workSession/restore",
                        WorkSessionRestoreParams(
                            project_id=params.project_id,
                            work_session_id=session_id,
                            sequence=preview.sequence,
                            confirm_token=preview.confirm_token,
                        ),
                        WorkSessionHistoryResult,
                    )
                ),
            )
        elif action == "integrate" and point is not None:
            preview = await self._vm.client.request(
                "workSession/integratePreview",
                WorkSessionIntegratePreviewParams(
                    project_id=params.project_id,
                    work_session_id=session_id,
                    sequence=point.sequence,
                ),
                MergePreviewResult,
            )
            if preview.blockers or "merge" not in preview.modes:
                self._toast(", ".join((*preview.blockers, *preview.probe.conflicts)))
                return
            self._ask(
                f"{preview.source_oid[:8]} → {preview.target_oid[:8]}\n{translate('git.session_integrate')}",
                lambda: self._start(
                    self._simple(
                        "workSession/integrate",
                        WorkSessionIntegrateParams(
                            project_id=params.project_id,
                            work_session_id=session_id,
                            sequence=point.sequence,
                            source_oid=preview.source_oid,
                            target_oid=preview.target_oid,
                            confirm_token=preview.confirm_token,
                        ),
                        GitOperationResult,
                    )
                ),
            )
        elif action == "cleanup_worker" and point is not None:
            self._ask(
                translate("git.session_cleanup_hint"),
                lambda: self._start(
                    self._simple(
                        "workSession/cleanupWorker",
                        WorkSessionCleanupWorkerParams(
                            project_id=params.project_id, work_session_id=session_id, sequence=point.sequence
                        ),
                        EmptyResult,
                    )
                ),
            )

    async def _simple(self, method: str, params: msgspec.Struct, result: type[msgspec.Struct]) -> None:
        _ = await self._vm.client.request(method, params, result)

    async def _rename(self, params: WorkSessionParams, title: str) -> None:
        await self._simple(
            "workSession/rename",
            WorkSessionRenameParams(
                project_id=params.project_id,
                work_session_id=params.work_session_id,
                title=title,
            ),
            WorkSessionView,
        )

    async def _handoff(self, params: WorkSessionParams, tab_id: TabId) -> None:
        tab = await self._vm.client.request(
            "workSession/handoff",
            WorkSessionHandoffParams(
                project_id=params.project_id,
                work_session_id=params.work_session_id,
                to_tab=tab_id,
            ),
            TabView,
        )
        if self._resume_tab is not None:
            self._resume_tab(tab.id)
