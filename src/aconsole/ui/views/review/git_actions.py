"""The buttons that change Git, and the dialogs in front of them.

The selected row decides which buttons show. Anything hard to undo goes through a preview from the core, and the
dialog states what that preview said. The core checks everything again, so a refusal here is a message, not a bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

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
    "commit",
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
    def __init__(self, host: GitHost, bar: Gtk.Box) -> None:
        self._host: GitHost = host
        self._bar: Gtk.Box = bar
        self._revision: str | None = None
        self.dialog: Adw.AlertDialog | None = None
        self.message_view: Gtk.TextView | None = None
        self.mode_choice: Gtk.DropDown | None = None
        self.choice: Gtk.DropDown | None = None
        self.facts: tuple[FactReviewView, ...] = ()
        self.status: Gtk.Label = Gtk.Label()
        self.status.add_css_class("dim-label")
        bar.append(self.status)
        self.facts_button: Gtk.Button = Gtk.Button(visible=False)
        _ = self.facts_button.connect("clicked", self._facts_clicked)
        bar.append(self.facts_button)
        self.buttons: dict[str, Gtk.Button] = {}
        for key in _ORDER:
            button = Gtk.Button(label=translate(f"git.do.{key}"), visible=False)
            _ = button.connect("clicked", self._clicked, key)
            self.buttons[key] = button
            bar.append(button)

    def show_state(self, state: GitStateResult) -> None:
        self.refresh()
        running = open_operations(state)
        self.status.set_text(
            "  ".join(translate("git.op_line").format(kind=item.kind, status=item.status) for item in running),
        )
        if state.revision != self._revision:
            self._revision = state.revision
            _ = spawn_task(self._load_facts())

    def refresh(self) -> None:
        offered = self._offered()
        for key, button in self.buttons.items():
            button.set_visible(key in offered)

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

    def _clicked(self, _button: Gtk.Button, key: str) -> None:  # noqa: C901, PLR0912 -- one line per button
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
            self._start(self._stage(worktree))
        elif key == "unstage":
            self._start(self._unstage(worktree))
        elif key == "commit":
            self._commit_dialog(worktree)
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
        """Whatever the answer, the page shows what is true now."""
        try:
            await work
        except CoreCallError as error:
            self._host.toast(refusal_text(error))
        except Exception as error:  # noqa: BLE001 -- a lost core shows here, not in a crash
            self._host.toast(translate("review.load_error").format(error=error))
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

    def _chosen_paths(self, kind: str) -> tuple[str, ...]:
        """The file in view when the inspector shows that side. Otherwise everything."""
        source = self._host.source
        path = self._host.selected_path
        if path is not None and source is not None and source.kind == kind:
            return (path,)
        return _ALL_FILES

    async def _stage(self, row: WorktreeGitState) -> None:
        done = await self._host.viewmodel.stage(row, self._chosen_paths("worktree"))
        if not done.ok:
            self._say(done, "")

    async def _unstage(self, row: WorktreeGitState) -> None:
        done = await self._host.viewmodel.unstage(row, self._chosen_paths("index"))
        if not done.ok:
            self._say(done, "")

    def _commit_dialog(self, row: WorktreeGitState) -> None:
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        self.message_view = view
        frame = Gtk.ScrolledWindow(child=view, min_content_height=_MESSAGE_HEIGHT)
        frame.add_css_class("card")

        def commit() -> None:
            buffer = view.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), include_hidden_chars=False).strip()
            if text:
                self._start(self._commit(row, text))

        self._ask(
            translate("git.commit_heading").format(branch=row.branch or translate("git.detached")),
            translate("git.commit_body").format(count=row.staged),
            translate("git.commit_go"),
            commit,
            extra=frame,
        )

    async def _commit(self, row: WorktreeGitState, message: str) -> None:
        done = await self._host.viewmodel.commit(row, message)
        self._say(done, translate("git.committed").format(oid=short(done.head_oid)))

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

        def merge() -> None:
            mode = modes[choice.get_selected()]
            self._start(self._do_merge(preview, mode, entry.get_text().strip() or None))

        self._ask(heading, "\n".join(lines), translate("git.merge_go"), merge, extra=box)

    async def _do_merge(self, preview: MergePreviewResult, mode: MergeMode, message: str | None) -> None:
        operation = (await self._host.viewmodel.merge(preview, mode, message)).operation
        if operation.status in {"finished", "merged"}:
            self._host.toast(translate("git.merged").format(oid=short(operation.result_oid)))
        else:
            self._say_operation(operation)

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
        """A hint beside the work. If it cannot be read, the button just stays away."""
        try:
            found = await self._host.viewmodel.facts()
        except CoreCallError:
            return
        except Exception:  # noqa: BLE001 -- the page's own load reports a lost core
            return
        self.facts = found.facts
        self.facts_button.set_visible(bool(found.facts))
        self.facts_button.set_label(translate("git.facts").format(count=len(found.facts)))

    def _facts_clicked(self, _button: Gtk.Button) -> None:
        lines = [
            translate("git.facts_line").format(title=item.title, paths=", ".join(item.changed_paths))
            for item in self.facts
        ]
        self._ask(
            translate("git.facts_heading"),
            "\n".join((translate("git.facts_body"), "", *lines)),
            None,
            _nothing,
        )


def _nothing() -> None:
    return
