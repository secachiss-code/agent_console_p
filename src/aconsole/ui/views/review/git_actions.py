"""The buttons that change Git, and the dialogs in front of them.

The selected row decides which buttons show. Anything hard to undo goes through a preview from the core, and the
dialog states what that preview said. The core checks everything again, so a refusal here is a message, not a bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreCallError  # noqa: E402
from aconsole.ui.viewmodels.git_vm import (  # noqa: E402
    GitViewModel,
    GraphRow,
    actions_for,
    format_size,
    merge_source,
    open_operations,
    recipients,
    row_flags,
    short,
    worktree_of,
)
from aconsole.ui.views.review.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from aconsole.contracts.git import (
        DiffSource,
        FactReviewView,
        GitChangeResult,
        GitOperationView,
        GitStateResult,
        MergeMode,
        MergePreviewResult,
        RestoreFilePreviewResult,
        SendDiffPreviewResult,
        WorktreeGitState,
    )

_ORDER: Final = (
    "continue",
    "abort",
    "stage",
    "unstage",
    "merge",
    "rebase",
    "cherry_pick",
    "restore",
    "send",
    "cleanup",
)
_ALL_FILES: Final = (".",)
_KNOWN_ERRORS: Final = frozenset(
    {
        "GIT_STATE_STALE",
        "MERGE_PREVIEW_STALE",
        "WORKTREE_BUSY",
        "GIT_OPERATION_IN_PROGRESS",
        "MERGE_CONFLICT",
        "MERGE_MODE_UNAVAILABLE",
        "CONFIRM_TOKEN_INVALID",
        "DATABASE_TRANSACTION_FAILED",
    }
)
_NOT_REMOVABLE: Final = "WORKTREE_NOT_REMOVABLE"
_TOO_LARGE: Final = "DIFF_TOO_LARGE"
_IGNORED: Final = "ignored"
_GO: Final = "go"
_MESSAGE_HEIGHT: Final = 110
_SHOWN_CONFLICTS: Final = 6


class GitHost(Protocol):
    """What these actions need from the page they sit on."""

    viewmodel: GitViewModel
    state: GitStateResult | None
    selected_path: str | None
    source: DiffSource | None
    toast: Callable[[str], None]

    def selected_row(self) -> GraphRow | None: ...

    def reload(self) -> None: ...


def _reason(error: CoreCallError) -> str | None:
    data = error.error.data
    return None if data is None else data.user_message_args.get("reason")


def refusal_text(error: CoreCallError) -> str:
    name = error.error.message
    if name in _KNOWN_ERRORS:
        return translate(f"git.err.{name.lower()}")
    if name == _NOT_REMOVABLE:
        return translate(f"git.err.not_removable.{_reason(error)}")
    return translate("git.err.other").format(name=name)


class GitActions:
    def __init__(self, host: GitHost, bar: Gtk.FlowBox, commit_slot: Gtk.Box, banner: Adw.Banner) -> None:
        self._host: GitHost = host
        self._bar: Gtk.FlowBox = bar
        self.banner: Adw.Banner = banner
        self._note_path: str | None = None
        self._revision: str | None = None
        self.dialog: Adw.AlertDialog | None = None
        self.cleanup_choice: Gtk.CheckButton | None = None
        self.mode_choice: Gtk.DropDown | None = None
        self.choice: Gtk.DropDown | None = None
        self.facts: tuple[FactReviewView, ...] = ()
        self.status: Gtk.Label = Gtk.Label(xalign=0)
        self.status.add_css_class("dim-label")
        bar.append(self.status)
        self.spinner: Gtk.Spinner = Gtk.Spinner(visible=False)
        bar.append(self.spinner)
        self._parent_of(self.spinner).set_visible(False)
        self._parent_of(self.status).set_visible(False)
        _ = banner.connect("button-clicked", self._facts_clicked)
        self.file_actions: dict[tuple[str, str], Gtk.Button] = {}
        self.buttons: dict[str, Gtk.Button] = {}
        for key in _ORDER:
            button = Gtk.Button(label=translate(f"git.do.{key}"))
            _ = button.connect("clicked", self._clicked, key)
            self.buttons[key] = button
            bar.append(button)
            self._parent_of(button).set_visible(False)
        self.commit_counts: Gtk.Label = Gtk.Label(xalign=0)
        self.commit_counts.add_css_class("dim-label")
        self.message_view: Gtk.TextView = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.message_view.add_css_class("ac-git-message")
        self.message_view.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("git.commit_placeholder"))]
        )
        _ = self.message_view.get_buffer().connect("changed", self._message_changed)
        self.commit_button: Gtk.Button = Gtk.Button(label=translate("git.commit_go"), halign=Gtk.Align.END)
        self.commit_button.add_css_class("suggested-action")
        _ = self.commit_button.connect("clicked", self._commit_clicked)
        self._commit_slot: Gtk.Box = commit_slot
        commit_slot.add_css_class("ac-git-commit")
        commit_slot.append(self.commit_counts)
        commit_slot.append(Gtk.ScrolledWindow(child=self.message_view, min_content_height=_MESSAGE_HEIGHT))
        commit_slot.append(self.commit_button)
        commit_slot.set_visible(False)

    def _parent_of(self, widget: Gtk.Widget) -> Gtk.Widget:
        parent = widget.get_parent()
        return widget if parent is None else parent

    def show_state(self, state: GitStateResult) -> None:
        self.refresh()
        running = open_operations(state)
        self.status.set_text(
            "  ".join(translate("git.op_line").format(kind=item.kind, status=item.status) for item in running),
        )
        self._parent_of(self.status).set_visible(bool(running))
        if state.revision != self._revision:
            self._revision = state.revision
            _ = spawn_task(self._load_facts())

    def refresh(self) -> None:
        offered = self._offered()
        for key, button in self.buttons.items():
            self._parent_of(button).set_visible(key in offered)
        row = self._host.selected_row()
        state = self._host.state
        worktree = None if row is None or row.worktree is None else worktree_of(state, row)
        self._commit_slot.set_visible(worktree is not None and not worktree.busy and worktree.in_progress is None)
        self._update_commit()

    def offered(self, key: str) -> bool:
        return self._parent_of(self.buttons[key]).get_visible()

    def _commit_row(self) -> WorktreeGitState | None:
        row = self._host.selected_row()
        return None if row is None else worktree_of(self._host.state, row)

    def _message(self) -> str:
        buffer = self.message_view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), include_hidden_chars=False).strip()

    def _update_commit(self) -> None:
        row = self._commit_row()
        state = self._host.state
        flags = row_flags(row, None if state is None else state.base_branch)
        self.commit_counts.set_text(translate("git.commit_counts").format(count=0 if row is None else row.staged))
        self.commit_button.set_sensitive(flags.can_commit and bool(self._message()))

    def _message_changed(self, _buffer: Gtk.TextBuffer) -> None:
        self._update_commit()

    def _commit_clicked(self, _button: Gtk.Button) -> None:
        row = self._commit_row()
        message = self._message()
        if row is not None and message:
            self._start(self._commit(row, message))

    def file_buttons(self, path: str) -> tuple[Gtk.Widget, ...]:
        """Per file, on the side of the diff the inspector is showing."""
        source = self._host.source
        row = self._commit_row()
        if source is None or row is None or row.busy:
            return ()
        if source.kind == "index":
            return (self._file_button("list-remove-symbolic", "git.file_unstage", self._unstage_file, path),)
        if source.kind != "worktree":
            return ()
        return (
            self._file_button("list-add-symbolic", "git.file_stage", self._stage_file, path),
            self._file_button("edit-undo-symbolic", "git.file_discard", self._discard_file, path),
        )

    def _file_button(
        self, icon: str, tip: str, action: Callable[[Gtk.Button, str], None], path: str
    ) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, tooltip_text=translate(tip), valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.update_property([Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate(tip))])
        _ = button.connect("clicked", action, path)
        self.file_actions[(tip, path)] = button
        return button

    def _stage_file(self, _button: Gtk.Button, path: str) -> None:
        row = self._commit_row()
        if row is not None:
            self._start(self._stage(row, (path,)))

    def _unstage_file(self, _button: Gtk.Button, path: str) -> None:
        row = self._commit_row()
        if row is not None:
            self._start(self._unstage(row, (path,)))

    def _discard_file(self, _button: Gtk.Button, path: str) -> None:
        row = self._commit_row()
        if row is not None and row.head_oid is not None:
            self._start(self._open_discard(row, path, row.head_oid))

    async def _open_discard(self, row: WorktreeGitState, path: str, oid: str) -> None:
        preview = await self._host.viewmodel.restore_preview(row.path, path, oid)

        def discard() -> None:
            self._start(self._do_restore(row, preview))

        if preview.exists_in_source:
            body = translate("git.discard_body").format(
                oid=short(oid), insertions=preview.insertions, deletions=preview.deletions
            )
        else:
            body = translate("git.discard_removes")
        self._ask(
            translate("git.discard_heading").format(path=path),
            body,
            translate("git.discard_go"),
            discard,
            destructive=True,
        )

    def _offered(self) -> tuple[str, ...]:
        row = self._host.selected_row()
        if row is None:
            return ()
        worktree = worktree_of(self._host.state, row)
        state = self._host.state
        return actions_for(
            worktree,
            row.node,
            base_branch=None if state is None else state.base_branch,
            has_recipient=bool(recipients(state, worktree)),
            has_file=self._host.selected_path is not None,
        )

    def _clicked(self, _button: Gtk.Button, key: str) -> None:  # noqa: C901 -- one line per button
        row = self._host.selected_row()
        if row is None:
            return
        worktree = worktree_of(self._host.state, row)
        if key == "merge":
            self._merge(row, worktree)
        elif key == "cherry_pick":
            self._cherry_pick(row)
        elif key == "restore":
            self._restore(row)
        elif key == "send":
            self._send(worktree)
        elif worktree is None:
            return
        elif key == "stage":
            self._start(self._stage(worktree, _ALL_FILES))
        elif key == "unstage":
            self._start(self._unstage(worktree, _ALL_FILES))
        elif key == "continue":
            self._start(self._resume(worktree))
        elif key == "abort":
            self._abort_dialog(worktree)
        elif key == "rebase":
            self._rebase_dialog(worktree)
        elif key == "cleanup":
            self._cleanup_dialog(worktree)

    def _start(self, work: Coroutine[object, object, None]) -> None:
        _ = spawn_task(self._guarded(work))

    async def _guarded(self, work: Coroutine[object, object, None]) -> None:
        """One at a time, and whatever the answer, the page shows what is true now."""
        self._bar.set_sensitive(False)
        self.commit_button.set_sensitive(False)
        self._parent_of(self.spinner).set_visible(True)
        self.spinner.set_visible(True)
        self.spinner.start()
        try:
            await work
        except CoreCallError as error:
            self._host.toast(refusal_text(error))
        except Exception as error:  # noqa: BLE001 -- a lost core shows here, not in a crash
            self._host.toast(translate("review.load_error").format(error=error))
        finally:
            self.spinner.stop()
            self._parent_of(self.spinner).set_visible(False)
            self._bar.set_sensitive(True)
            self._update_commit()
        self._host.reload()

    def _ask(  # noqa: PLR0913 -- one dialog, in the words of its caller
        self,
        heading: str,
        body: str,
        go: str | None,
        on_go: Callable[[], None],
        *,
        destructive: bool = False,
        extra: Gtk.Widget | None = None,
    ) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", translate("git.cancel" if go is not None else "git.close"))
        if go is not None:
            dialog.add_response(_GO, go)
            if destructive:
                dialog.set_response_appearance(_GO, Adw.ResponseAppearance.DESTRUCTIVE)
            else:
                dialog.set_response_appearance(_GO, Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        if extra is not None:
            dialog.set_extra_child(extra)

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == _GO:
                on_go()

        _ = dialog.connect("response", answered)
        self.dialog = dialog
        if isinstance(self._host, Gtk.Widget):
            dialog.present(self._host)

    def _choose(
        self, heading: str, labels: list[str], on_pick: Callable[[int], None], *, go: str | None = None
    ) -> None:
        """One choice. With only one possible answer, it is not asked."""
        if len(labels) == 1:
            on_pick(0)
            return
        choice = Gtk.DropDown.new_from_strings(labels)
        self.choice = choice

        def picked() -> None:
            on_pick(choice.get_selected())

        self._ask(heading, "", go or translate("git.next"), picked, extra=choice)

    def _say(self, done: GitChangeResult, ok: str) -> None:
        if done.ok:
            self._host.toast(ok)
        else:
            self._host.toast(translate("git.refused").format(detail=done.detail or ""))

    def _say_operation(self, operation: GitOperationView) -> None:
        if operation.status == "conflicted":
            self._host.toast(translate("git.op_conflicted"))
        elif operation.status == "failed":
            self._host.toast(translate("git.op_failed").format(detail=operation.message or ""))
        else:
            self._host.toast(translate("git.op_done").format(oid=short(operation.result_oid)))

    async def _stage(self, row: WorktreeGitState, paths: tuple[str, ...]) -> None:
        done = await self._host.viewmodel.stage(row, paths)
        if not done.ok:
            self._say(done, "")

    async def _unstage(self, row: WorktreeGitState, paths: tuple[str, ...]) -> None:
        done = await self._host.viewmodel.unstage(row, paths)
        if not done.ok:
            self._say(done, "")

    async def _commit(self, row: WorktreeGitState, message: str) -> None:
        done = await self._host.viewmodel.commit(row, message)
        self._say(done, translate("git.committed").format(oid=short(done.head_oid)))
        if done.ok:
            self.message_view.get_buffer().set_text("")

    async def _resume(self, row: WorktreeGitState) -> None:
        done = await self._host.viewmodel.resume(row.path)
        self._say(done, translate("git.resumed"))

    def _abort_dialog(self, row: WorktreeGitState) -> None:
        def abort() -> None:
            self._start(self._abort(row))

        self._ask(
            translate("git.abort_heading").format(kind=row.in_progress),
            translate("git.abort_body"),
            translate("git.abort_go"),
            abort,
            destructive=True,
        )

    async def _abort(self, row: WorktreeGitState) -> None:
        done = await self._host.viewmodel.abort(row.path)
        self._say(done, translate("git.aborted"))

    def _rebase_dialog(self, row: WorktreeGitState) -> None:
        state = self._host.state
        if state is None or state.base_oid is None:
            return
        onto = state.base_oid

        def rebase() -> None:
            self._start(self._rebase(row, onto))

        self._ask(
            translate("git.rebase_heading").format(branch=row.branch, base=state.base_branch),
            translate("git.rebase_body"),
            translate("git.rebase_go"),
            rebase,
        )

    async def _rebase(self, row: WorktreeGitState, onto: str) -> None:
        self._say_operation((await self._host.viewmodel.rebase(row, onto)).operation)

    def _merge(self, row: GraphRow, worktree: WorktreeGitState | None) -> None:
        state = self._host.state
        source = merge_source(worktree, row.node, None if state is None else state.base_branch)
        if source is not None:
            self._start(self._open_merge(source))

    async def _open_merge(self, source: str) -> None:
        self._merge_dialog(await self._host.viewmodel.merge_preview(source))

    def _merge_dialog(self, preview: MergePreviewResult) -> None:
        lines = [
            translate("git.merge_body").format(
                source_oid=short(preview.source_oid),
                target_oid=short(preview.target_oid),
                insertions=preview.probe.insertions,
                deletions=preview.probe.deletions,
            )
        ]
        if preview.source_dirty:
            lines.append(translate("git.merge_source_dirty").format(source=preview.source))
        lines.extend(translate(f"git.block.{item}") for item in preview.blockers)
        if preview.probe.conflicts:
            paths = ", ".join(preview.probe.conflicts[:_SHOWN_CONFLICTS])
            lines.append(translate("git.merge_conflicts").format(paths=paths))
        heading = translate("git.merge_heading").format(source=preview.source, target=preview.target)
        if preview.blockers or not preview.modes:
            self._ask(heading, "\n".join(lines), None, _nothing)
            return
        modes = preview.modes
        choice = Gtk.DropDown.new_from_strings([translate(f"git.mode.{mode}") for mode in modes])
        self.mode_choice = choice
        entry = Gtk.Entry(placeholder_text=translate("git.merge_message"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(choice)
        box.append(entry)
        removable = self._removable_after(preview.source)
        cleanup = Gtk.CheckButton(label=translate("git.merge_cleanup"), active=True, visible=removable is not None)
        self.cleanup_choice = cleanup
        box.append(cleanup)

        def merge() -> None:
            mode = modes[choice.get_selected()]
            then = removable if cleanup.get_active() else None
            self._start(self._do_merge(preview, mode, entry.get_text().strip() or None, then))

        self._ask(heading, "\n".join(lines), translate("git.merge_go"), merge, extra=box)

    def _removable_after(self, branch: str) -> WorktreeGitState | None:
        state = self._host.state
        rows = () if state is None else state.worktrees
        return next((row for row in rows if row.branch == branch and not row.is_main), None)

    async def _do_merge(
        self,
        preview: MergePreviewResult,
        mode: MergeMode,
        message: str | None,
        then_remove: WorktreeGitState | None,
    ) -> None:
        operation = (await self._host.viewmodel.merge(preview, mode, message)).operation
        if operation.status not in {"finished", "merged"}:
            self._say_operation(operation)
            return
        self._host.toast(translate("git.merged").format(oid=short(operation.result_oid)))
        if then_remove is not None:
            await self._cleanup(then_remove, discard_ignored=False)

    def _cleanup_dialog(self, row: WorktreeGitState) -> None:
        def remove() -> None:
            self._start(self._cleanup(row, discard_ignored=False))

        self._ask(
            translate("git.cleanup_heading").format(branch=row.branch or translate("git.detached")),
            translate("git.cleanup_body").format(path=row.path),
            translate("git.cleanup_go"),
            remove,
            destructive=True,
        )

    async def _cleanup(self, row: WorktreeGitState, *, discard_ignored: bool) -> None:
        try:
            done = await self._host.viewmodel.cleanup(row.path, discard_ignored=discard_ignored)
        except CoreCallError as error:
            if error.error.message != _NOT_REMOVABLE or _reason(error) != _IGNORED:
                raise

            def remove_all() -> None:
                self._start(self._cleanup(row, discard_ignored=True))

            self._ask(
                translate("git.cleanup_ignored_heading"),
                translate("git.cleanup_ignored_body").format(path=row.path),
                translate("git.cleanup_ignored_go"),
                remove_all,
                destructive=True,
            )
            return
        if done.kept_ref is not None:
            self._host.toast(translate("git.cleaned_kept").format(ref=done.kept_ref))
        else:
            self._host.toast(translate("git.cleaned"))

    def _worktrees(self) -> tuple[WorktreeGitState, ...]:
        state = self._host.state
        return () if state is None else tuple(item for item in state.worktrees if not item.busy)

    def _pick_worktree(self, heading: str, on_pick: Callable[[WorktreeGitState], None]) -> None:
        rows = self._worktrees()
        if not rows:
            self._host.toast(translate("git.err.worktree_busy"))
            return

        def picked(at: int) -> None:
            on_pick(rows[at])

        labels = [item.branch or item.path for item in rows]
        self._choose(heading, labels, picked)

    def _cherry_pick(self, row: GraphRow) -> None:
        node = row.node
        if node is None:
            return
        oid = node.oid

        def into(target: WorktreeGitState) -> None:
            self._start(self._do_cherry_pick(target, oid))

        self._pick_worktree(translate("git.cherry_heading").format(oid=short(oid)), into)

    async def _do_cherry_pick(self, target: WorktreeGitState, oid: str) -> None:
        self._say_operation((await self._host.viewmodel.cherry_pick(target, oid)).operation)

    def _restore(self, row: GraphRow) -> None:
        node = row.node
        path = self._host.selected_path
        if node is None or path is None:
            return
        oid = node.oid

        def into(target: WorktreeGitState) -> None:
            self._start(self._open_restore(target, path, oid))

        self._pick_worktree(translate("git.pick_worktree"), into)

    async def _open_restore(self, target: WorktreeGitState, path: str, oid: str) -> None:
        preview = await self._host.viewmodel.restore_preview(target.path, path, oid)

        def restore() -> None:
            self._start(self._do_restore(target, preview))

        name = target.branch or target.path
        if preview.exists_in_source:
            body = translate("git.restore_body").format(
                worktree=name, oid=short(oid), insertions=preview.insertions, deletions=preview.deletions
            )
        else:
            body = translate("git.restore_removes").format(worktree=name, oid=short(oid))
        self._ask(
            translate("git.restore_heading").format(path=path),
            body,
            translate("git.restore_go"),
            restore,
            destructive=True,
        )

    async def _do_restore(self, target: WorktreeGitState, preview: RestoreFilePreviewResult) -> None:
        done = await self._host.viewmodel.restore(target.path, preview)
        self._say(done, translate("git.restored").format(path=preview.path))

    def _send(self, worktree: WorktreeGitState | None) -> None:
        source = self._host.source
        rows = recipients(self._host.state, worktree)
        if source is None or not rows:
            return

        def picked(at: int) -> None:
            self._start(self._open_send(source, rows[at]))

        self._choose(translate("git.send_heading"), [row.tab_title or row.path for row in rows], picked)

    async def _open_send(self, source: DiffSource, row: WorktreeGitState) -> None:
        if row.tab_id is None:
            return
        try:
            preview = await self._host.viewmodel.send_preview(source, row.tab_id)
        except CoreCallError as error:
            if error.error.message != _TOO_LARGE:
                raise
            self._host.toast(translate("git.send_too_large"))
            return
        self._send_dialog(source, preview)

    def _send_dialog(self, source: DiffSource, preview: SendDiffPreviewResult) -> None:
        def send() -> None:
            self._start(self._do_send(source, preview))

        self._ask(
            translate("git.send_confirm_heading").format(title=preview.to_title),
            translate("git.send_body").format(files=preview.files, size=format_size(preview.byte_count)),
            translate("git.send_go"),
            send,
        )

    async def _do_send(self, source: DiffSource, preview: SendDiffPreviewResult) -> None:
        done = await self._host.viewmodel.send(source, preview)
        self._host.toast(done.toast or translate("git.sent"))

    async def _load_facts(self) -> None:
        """A hint beside the work. If it cannot be read, the banner just stays away."""
        try:
            found = await self._host.viewmodel.facts()
        except CoreCallError:
            return
        except Exception:  # noqa: BLE001 -- the page's own load reports a lost core
            return
        self.facts = found.facts
        self._note_path = found.note_path
        self.banner.set_title(translate("git.facts").format(count=len(found.facts)))
        self.banner.set_revealed(bool(found.facts))

    def _facts_clicked(self, _banner: Adw.Banner) -> None:
        lines = [
            translate("git.facts_line").format(title=item.title, paths=", ".join(item.changed_paths))
            for item in self.facts
        ]
        go = None if self._note_path is None else translate("git.facts_open")
        self._ask(
            translate("git.facts_heading"),
            "\n".join((translate("git.facts_body"), "", *lines)),
            go,
            self._open_note,
        )

    def _open_note(self) -> None:
        if self._note_path is None:
            return
        uri = f"obsidian://open?path={quote(self._note_path, safe='')}"
        window = self._host.get_root() if isinstance(self._host, Gtk.Widget) else None
        Gtk.UriLauncher.new(uri).launch(window if isinstance(window, Gtk.Window) else None, None, None, None)


def _nothing() -> None:
    return
