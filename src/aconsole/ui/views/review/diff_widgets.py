"""Diff rows and hunks, shared by a tab's Changes page and the Git inspector."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from aconsole.contracts.app import DiffFileView, DiffHunk  # noqa: E402
from aconsole.ui.viewmodels.review_vm import gutter  # noqa: E402

_LINE_TAGS = {"add": "ac-add", "del": "ac-del", "meta": "ac-meta"}
_ADD = "rgba(46, 160, 67, 0.20)"
_DEL = "rgba(248, 81, 73, 0.20)"
_WORD_ADD = "rgba(46, 160, 67, 0.45)"
_WORD_DEL = "rgba(248, 81, 73, 0.45)"


def file_row(item: DiffFileView) -> Gtk.ListBoxRow:
    box = Gtk.Box(spacing=8)
    box.add_css_class("ac-review-file")
    name = Gtk.Label(label=item.path, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
    name.set_tooltip_text(item.path if item.old_path is None else f"{item.old_path} → {item.path}")
    box.append(name)
    counts = Gtk.Label(label=f"+{item.insertions} −{item.deletions}")  # noqa: RUF001 -- the minus sign
    counts.add_css_class("dim-label")
    counts.add_css_class("numeric")
    box.append(counts)
    return Gtk.ListBoxRow(child=box)


def hunk_text(hunk: DiffHunk) -> Gtk.TextView:
    """One text view per hunk: a label per line would not hold 2000 lines."""
    buffer = Gtk.TextBuffer()
    _ = buffer.create_tag("ac-add", paragraph_background=_ADD)
    _ = buffer.create_tag("ac-del", paragraph_background=_DEL)
    _ = buffer.create_tag("ac-meta", style=Pango.Style.ITALIC)
    _ = buffer.create_tag("ac-word-add", background=_WORD_ADD)
    _ = buffer.create_tag("ac-word-del", background=_WORD_DEL)
    for number, line in enumerate(hunk.lines):
        prefix = gutter(line.old_no, line.new_no, line.kind)
        tail = "" if number == len(hunk.lines) - 1 else "\n"
        start = buffer.get_end_iter().get_offset()
        buffer.insert(buffer.get_end_iter(), f"{prefix}{line.text}{tail}")
        tag = _LINE_TAGS.get(line.kind)
        if tag is not None:
            buffer.apply_tag_by_name(tag, buffer.get_iter_at_offset(start), buffer.get_end_iter())
        if line.kind in ("add", "del"):
            word = "ac-word-add" if line.kind == "add" else "ac-word-del"
            text_start = start + len(prefix)
            for begin, end in line.word_spans:
                buffer.apply_tag_by_name(
                    word, buffer.get_iter_at_offset(text_start + begin), buffer.get_iter_at_offset(text_start + end),
                )
    view = Gtk.TextView(buffer=buffer, editable=False, cursor_visible=False, monospace=True, hexpand=True)
    view.add_css_class("ac-review-text")
    return view
