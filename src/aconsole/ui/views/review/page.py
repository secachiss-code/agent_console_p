"""One tab's changes and checkpoints, in place of its terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import CheckpointView, DiffFileResult, DiffFileView, DiffHunk  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreCallError  # noqa: E402
from aconsole.ui.viewmodels.review_vm import ReviewViewModel, files_preview  # noqa: E402
from aconsole.ui.views.review.diff_widgets import file_row, hunk_text  # noqa: E402
from aconsole.ui.views.review.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

_PANE_POSITION = 280
_ISLAND_CLEARANCE = 72


class ReviewPage(Gtk.Box):
    def __init__(
        self,
        viewmodel: ReviewViewModel,
        title: str,
        on_close: Callable[[], None],
        on_toast: Callable[[str], None],
        git_pane: Gtk.Widget | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("ac-plan-page")
        self.viewmodel: ReviewViewModel = viewmodel
        self._on_close: Callable[[], None] = on_close
        self._toast: Callable[[str], None] = on_toast
        self._generation: int = 0
        self._file_generation: int = 0
        self._file_paths: list[str] = []
        self.selected_path: str | None = None
        self.stack: Adw.ViewStack = Adw.ViewStack(vexpand=True)
        self.append(self._build_heading(title))
        self.append(self.stack)
        if git_pane is not None:
            _ = self.stack.add_titled_with_icon(
                git_pane, "git", translate("git.title"), "media-playlist-shuffle-symbolic",
            )
        _ = self.stack.add_titled_with_icon(
            self._build_changes(), "changes", translate("review.changes"), "document-edit-symbolic",
        )
        _ = self.stack.add_titled_with_icon(
            self._build_checkpoints(), "checkpoints", translate("review.checkpoints"), "document-revert-symbolic",
        )
        self.reload()

    def reload(self) -> None:
        _ = spawn_task(self.load_changes())
        _ = spawn_task(self.load_checkpoints())

    def _build_heading(self, title: str) -> Gtk.Box:
        heading = Gtk.Box(spacing=12)
        heading.add_css_class("ac-plan-heading")
        # The window's end island floats over this corner.
        heading.set_margin_end(_ISLAND_CLEARANCE)
        self.close_button: Gtk.Button = Gtk.Button(label=translate("review.back"))
        self.close_button.add_css_class("ac-plan-back")
        _ = self.close_button.connect("clicked", self._close)
        heading.append(self.close_button)
        name = Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("title-4")
        heading.append(name)
        self.switcher: Adw.ViewSwitcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        heading.append(self.switcher)
        return heading

    def _close(self, _button: Gtk.Button) -> None:
        self._on_close()

    def _build_changes(self) -> Gtk.Paned:
        self.file_list: Gtk.ListBox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.file_list.add_css_class("navigation-sidebar")
        _ = self.file_list.connect("row-selected", self._file_selected)
        files = Gtk.ScrolledWindow(child=self.file_list, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.diff_box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.diff_box.add_css_class("ac-review-diff")
        scrolled = Gtk.ScrolledWindow(child=self.diff_box, hexpand=True)
        paned = Gtk.Paned(start_child=files, end_child=scrolled, position=_PANE_POSITION)
        paned.set_shrink_start_child(False)
        return paned

    def _build_checkpoints(self) -> Gtk.ScrolledWindow:
        self.checkpoint_list: Gtk.ListBox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.checkpoint_list.add_css_class("boxed-list")
        self.checkpoint_list.add_css_class("ac-review-checkpoints")
        self.checkpoint_list.set_valign(Gtk.Align.START)
        return Gtk.ScrolledWindow(child=self.checkpoint_list, hscrollbar_policy=Gtk.PolicyType.NEVER)

    async def load_changes(self) -> None:
        self._generation += 1
        generation = self._generation
        try:
            summary = await self.viewmodel.summary()
        except Exception as error:  # noqa: BLE001 -- the page stays open and says what went wrong
            if generation == self._generation:
                self._show_files_message(translate("review.load_error").format(error=error))
            return
        if generation == self._generation:
            self._show_files(summary.files)

    def _show_files_message(self, text: str) -> None:
        self.file_list.remove_all()
        self._file_paths = []
        self._set_diff_message(text)

    def _show_files(self, files: tuple[DiffFileView, ...]) -> None:
        previous = self.selected_path
        self.file_list.remove_all()
        self._file_paths = [item.path for item in files]
        if not files:
            self.selected_path = None
            self._set_diff_message(translate("review.no_changes"))
            return
        for item in files:
            self.file_list.append(file_row(item))
        index = self._file_paths.index(previous) if previous in self._file_paths else 0
        self.file_list.select_row(self.file_list.get_row_at_index(index))

    def _file_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        self.selected_path = self._file_paths[row.get_index()]
        _ = spawn_task(self._load_file(self.selected_path))

    async def _load_file(self, path: str) -> None:
        self._file_generation += 1
        generation = self._file_generation
        try:
            detail = await self.viewmodel.file(path)
        except CoreCallError as error:
            if generation == self._file_generation:
                self._set_diff_message(
                    translate("review.too_large") if error.error.message == "DIFF_TOO_LARGE"
                    else translate("review.load_error").format(error=error),
                )
            return
        except Exception as error:  # noqa: BLE001 -- a lost core shows here, not in a crash
            if generation == self._file_generation:
                self._set_diff_message(translate("review.load_error").format(error=error))
            return
        if generation == self._file_generation:
            self._show_diff(path, detail)

    def _clear_diff(self) -> None:
        while (child := self.diff_box.get_first_child()) is not None:
            self.diff_box.remove(child)

    def _set_diff_message(self, text: str) -> None:
        self._clear_diff()
        label = Gtk.Label(label=text, wrap=True, vexpand=True)
        label.add_css_class("dim-label")
        self.diff_box.append(label)

    def _show_diff(self, path: str, detail: DiffFileResult) -> None:
        if not detail.hunks:
            self._set_diff_message(translate("review.binary"))
            return
        self._clear_diff()
        for hunk in detail.hunks:
            self.diff_box.append(self._hunk_widget(path, hunk))
        if detail.truncated:
            note = Gtk.Label(label=translate("review.truncated"), xalign=0)
            note.add_css_class("dim-label")
            self.diff_box.append(note)

    def _hunk_widget(self, path: str, hunk: DiffHunk) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("ac-review-hunk")
        top = Gtk.Box(spacing=8)
        top.add_css_class("ac-review-hunk-head")
        header = Gtk.Label(label=hunk.header, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        header.add_css_class("monospace")
        top.append(header)
        comment = Gtk.Button(label=translate("review.comment"))
        comment.add_css_class("flat")
        _ = comment.connect("clicked", self._ask_comment, path, hunk.id)
        top.append(comment)
        box.append(top)
        box.append(hunk_text(hunk))
        return box

    def _ask_comment(self, _button: Gtk.Button, path: str, hunk_id: str) -> None:
        dialog = Adw.AlertDialog(
            heading=translate("review.comment_heading"),
            body=translate("review.comment_body").format(path=path, hunk=hunk_id),
        )
        entry = Gtk.Entry(activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", translate("review.comment_cancel"))
        dialog.add_response("send", translate("review.comment_send"))
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("send")
        dialog.set_close_response("cancel")
        _ = dialog.connect("response", self._comment_answered, entry, path, hunk_id)
        dialog.present(self)

    def _comment_answered(
        self, _dialog: Adw.AlertDialog, response: str, entry: Gtk.Entry, path: str, hunk_id: str,
    ) -> None:
        text = entry.get_text().strip()
        if response == "send" and text:
            _ = spawn_task(self.send_comment(path, hunk_id, text))

    async def send_comment(self, path: str, hunk_id: str, text: str) -> None:
        try:
            result = await self.viewmodel.comment(path, hunk_id, text)
        except Exception:  # noqa: BLE001 -- the failure is told; the text is the user's to send again
            self._toast(translate("review.comment_error"))
            return
        self._toast(result.toast or translate("review.comment_sent"))

    async def load_checkpoints(self) -> None:
        try:
            points = await self.viewmodel.checkpoints()
        except Exception as error:  # noqa: BLE001 -- the page stays open and says what went wrong
            self._show_checkpoints_message(translate("review.load_error").format(error=error))
            return
        self.checkpoint_list.remove_all()
        if not points:
            self._show_checkpoints_message(translate("review.no_checkpoints"))
            return
        for point in points:
            self.checkpoint_list.append(self._checkpoint_row(point))

    def _show_checkpoints_message(self, text: str) -> None:
        self.checkpoint_list.remove_all()
        row = Adw.ActionRow(title=text)
        row.set_activatable(False)
        self.checkpoint_list.append(row)

    def _checkpoint_row(self, point: CheckpointView) -> Adw.ActionRow:
        row = Adw.ActionRow(title=_markup_safe(point.label))
        row.set_subtitle(
            translate("review.checkpoint_line").format(
                turn=point.turn,
                time=point.created_at.astimezone().strftime("%d.%m %H:%M"),
                files=point.files_changed,
                insertions=point.insertions,
                deletions=point.deletions,
            )
        )
        button = Gtk.Button(label=translate("review.restore"), valign=Gtk.Align.CENTER)
        _ = button.connect("clicked", self._ask_restore, point)
        row.add_suffix(button)
        return row

    def _ask_restore(self, _button: Gtk.Button, point: CheckpointView) -> None:
        _ = spawn_task(self.ask_restore(point))

    async def ask_restore(self, point: CheckpointView) -> None:
        try:
            preview = await self.viewmodel.preview(point.id)
        except Exception as error:  # noqa: BLE001 -- nothing was restored; the user is told why
            self._toast(translate("review.restore_error").format(error=error))
            return
        named, rest = files_preview(preview.files)
        lines = list(named)
        if rest:
            lines.append(translate("review.restore_more").format(count=rest))
        dialog = Adw.AlertDialog(
            heading=translate("review.restore_heading"),
            body=translate("review.restore_body").format(
                scope=preview.scope_note, count=len(preview.files), files="\n".join(lines),
            ),
        )
        dialog.add_response("cancel", translate("review.restore_cancel"))
        dialog.add_response("restore", translate("review.restore"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        _ = dialog.connect("response", self._restore_answered, point, preview.confirm_token)
        dialog.present(self)

    def _restore_answered(
        self, _dialog: Adw.AlertDialog, response: str, point: CheckpointView, token: str,
    ) -> None:
        if response == "restore":
            _ = spawn_task(self.restore(point, token))

    async def restore(self, point: CheckpointView, token: str) -> None:
        try:
            _ = await self.viewmodel.restore(point.id, token)
        except CoreCallError as error:
            self._toast(_restore_refusal(error))
            return
        except Exception as error:  # noqa: BLE001 -- nothing to undo here; the user is told
            self._toast(translate("review.restore_error").format(error=error))
            return
        self._toast(translate("review.restored"))
        self.reload()


def _restore_refusal(error: CoreCallError) -> str:
    if error.error.message == "CHECKPOINT_RESTORE_CONFLICT":
        return translate("review.restore_busy")
    if error.error.message == "CONFIRM_TOKEN_INVALID":
        return translate("review.restore_expired")
    return translate("review.restore_error").format(error=error)


def _markup_safe(text: str) -> str:
    """`Adw.PreferencesRow` titles are Pango markup by default; a prompt is free text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
