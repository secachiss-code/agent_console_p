"""Requests waiting for a decision, under whatever the window shows.

Nothing here takes the keyboard on its own: a new card must not catch what is being typed into a
terminal, so a person moves here with `next-attention`. The single-key bindings only work inside a card.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import ApprovalView, CommandDetail, EditDetail  # noqa: E402
from aconsole.contracts.common import ApprovalDecision, ApprovalId, ApprovalScope, TabId  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreCallError  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import CLOSED_CODES, ApprovalsViewModel, safe_text, time_left  # noqa: E402
from aconsole.ui.viewmodels.review_vm import files_preview  # noqa: E402
from aconsole.ui.views.approvals.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

_MAX_HEIGHT: Final = 320
_SHIFT_GUARD_S: Final = 0.6
_TICK_S: Final = 1


def _mono(text: str) -> Gtk.Label:
    label = Gtk.Label(
        label=safe_text(text), xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
    )
    # Selectable labels take focus and would leave a stop between a card and its buttons.
    label.set_focusable(False)
    label.add_css_class("monospace")
    label.add_css_class("ac-approval-command")
    return label


class ApprovalCard(Gtk.ListBoxRow):
    def __init__(self, view: ApprovalView, tab_title: str, panel: ApprovalPanel) -> None:
        super().__init__(activatable=False, selectable=False)
        self.add_css_class("ac-attention-card")
        self.view: ApprovalView = view
        self.busy: bool = False
        self._panel: ApprovalPanel = panel
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.add_css_class("ac-approval-body")
        body.append(self._heading(tab_title))
        risks = self._risks()
        if risks is not None:
            body.append(risks)
        if not isinstance(view.detail, CommandDetail) or view.detail.command != view.summary:
            body.append(Gtk.Label(
                label=safe_text(view.summary), xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
            ))
        self._detail(body)
        cwd = Gtk.Label(
            label=translate("approvals.cwd").format(cwd=safe_text(view.cwd)),
            xalign=0, ellipsize=Pango.EllipsizeMode.START,
        )
        cwd.add_css_class("dim-label")
        cwd.add_css_class("caption")
        body.append(cwd)
        body.append(self._buttons())
        self.set_child(body)

    def _heading(self, tab_title: str) -> Gtk.Box:
        heading = Gtk.Box(spacing=12)
        title = Gtk.Label(
            label=translate("approvals.title").format(
                tool=safe_text(self.view.tool), agent=self.view.agent.value, tab=tab_title,
            ),
            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
        )
        title.add_css_class("heading")
        heading.append(title)
        self.timer: Gtk.Label = Gtk.Label()
        self.timer.add_css_class("dim-label")
        self.timer.add_css_class("numeric")
        heading.append(self.timer)
        self.tick(datetime.now(UTC))
        return heading

    def _risks(self) -> Gtk.Box | None:
        if not self.view.risk:
            return None
        row = Gtk.Box(spacing=6)
        for risk in sorted(self.view.risk):
            pill = Gtk.Label(label=translate(f"approvals.risk.{risk.value}"))
            pill.add_css_class("ac-approval-risk")
            pill.add_css_class("caption")
            row.append(pill)
        return row

    def _detail(self, body: Gtk.Box) -> None:
        detail = self.view.detail
        if isinstance(detail, CommandDetail):
            body.append(_mono(detail.command))
            if detail.args_preview:
                body.append(_mono(detail.args_preview))
        elif isinstance(detail, EditDetail):
            named, rest = files_preview(detail.files)
            lines = list(named)
            if rest:
                lines.append(translate("approvals.more_files").format(count=rest))
            body.append(_mono("\n".join(lines)))
            if detail.diff_preview:
                body.append(_mono(detail.diff_preview))
        else:
            body.append(_mono(detail.json_preview))

    def _buttons(self) -> Gtk.Box:
        row = Gtk.Box(spacing=8)
        self.allow_button: Gtk.Button = Gtk.Button(label=translate("approvals.allow"))
        self.allow_button.add_css_class("suggested-action")
        _ = self.allow_button.connect("clicked", self._allow)
        row.append(self.allow_button)
        self.session_button: Gtk.Button | None = None
        if self.view.allow_tab_session:
            self.session_button = Gtk.Button(
                label=translate("approvals.allow_session"), tooltip_text=translate("approvals.allow_session_tip"),
            )
            _ = self.session_button.connect("clicked", self._allow_session)
            row.append(self.session_button)
        self.deny_button: Gtk.Button = Gtk.Button(label=translate("approvals.deny"))
        self.deny_button.add_css_class("destructive-action")
        _ = self.deny_button.connect("clicked", self._deny)
        row.append(self.deny_button)
        self.release_button: Gtk.Button = Gtk.Button(
            label=translate("approvals.release"), tooltip_text=translate("approvals.release_tip"),
            halign=Gtk.Align.END, hexpand=True,
        )
        self.release_button.add_css_class("flat")
        _ = self.release_button.connect("clicked", self._release)
        row.append(self.release_button)
        return row

    def tick(self, now: datetime) -> None:
        left = time_left(self.view.expires_at, now)
        self.timer.set_label(
            translate("approvals.time_up") if left is None else translate("approvals.left").format(time=left),
        )

    def _allow(self, _button: Gtk.Button) -> None:
        self._panel.decide(self, ApprovalDecision.ALLOW)

    def _allow_session(self, _button: Gtk.Button) -> None:
        self._panel.decide(self, ApprovalDecision.ALLOW, ApprovalScope.TAB_SESSION)

    def _deny(self, _button: Gtk.Button) -> None:
        self._panel.decide(self, ApprovalDecision.DENY)

    def _release(self, _button: Gtk.Button) -> None:
        self._panel.release(self)


class ApprovalPanel(Gtk.Box):
    def __init__(
        self,
        viewmodel: ApprovalsViewModel,
        *,
        on_toast: Callable[[str], None],
        on_open: Callable[[TabId], None],
        on_left: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.add_css_class("ac-approval-panel")
        self.viewmodel: ApprovalsViewModel = viewmodel
        self._toast: Callable[[str], None] = on_toast
        self._open: Callable[[TabId], None] = on_open
        self._left: Callable[[], None] = on_left
        self._clock: Callable[[], float] = clock
        self._approvals: tuple[ApprovalView, ...] = ()
        self._titles: Mapping[str, str] = {}
        self._answered: set[ApprovalId] = set()
        self._guard_until: float = 0.0
        self._timer: int | None = None
        self.cards: list[ApprovalCard] = []
        self.count_label: Gtk.Label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("ac-approval-count")
        self.count_label.add_css_class("heading")
        self.append(self.count_label)
        self.list_box: Gtk.ListBox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("background")
        self.append(Gtk.ScrolledWindow(
            child=self.list_box, hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True, max_content_height=_MAX_HEIGHT,
        ))

    def update(self, approvals: tuple[ApprovalView, ...], titles: Mapping[str, str]) -> None:
        self._approvals = approvals
        self._titles = titles
        self._sync()

    def _sync(self) -> None:
        wanted = [item for item in self._approvals if item.id not in self._answered]
        wanted_ids = {item.id for item in wanted}
        focused = self.focused_index()
        focused_card = None if focused is None else self.cards[focused]
        for card in [card for card in self.cards if card.view.id not in wanted_ids]:
            self.list_box.remove(card)
            self.cards.remove(card)
            # What was under the pointer or the key moved up: the next press must not land on it.
            self._guard_until = self._clock() + _SHIFT_GUARD_S
        known = {card.view.id for card in self.cards}
        for item in wanted:
            if item.id not in known:
                card = ApprovalCard(item, self._titles.get(str(item.tab_id), ""), self)
                self.cards.append(card)
                self.list_box.append(card)
        self.count_label.set_label(translate("approvals.count").format(count=len(self.cards)))
        self.set_visible(bool(self.cards))
        self._run_timer()
        if focused is not None and focused_card not in self.cards:
            if self.cards:
                _ = self.cards[min(focused, len(self.cards) - 1)].grab_focus()
            else:
                self._left()

    def focused_index(self) -> int | None:
        root = self.get_root()
        widget = None if root is None else root.get_focus()
        while widget is not None:
            if isinstance(widget, ApprovalCard):
                return self.cards.index(widget) if widget in self.cards else None
            widget = widget.get_parent()
        return None

    def step(self, delta: int) -> bool:
        """Focus the next or previous card. False when there is none to go to."""
        if not self.cards:
            return False
        index = self.focused_index()
        target = 0 if index is None else (index + delta) % len(self.cards)
        return self.cards[target].grab_focus()

    def decide_focused(self, decision: ApprovalDecision) -> None:
        index = self.focused_index()
        if index is not None:
            self.decide(self.cards[index], decision)

    def open_focused(self) -> None:
        index = self.focused_index()
        if index is not None:
            self._open(self.cards[index].view.tab_id)

    def decide(self, card: ApprovalCard, decision: ApprovalDecision, scope: ApprovalScope = ApprovalScope.ONCE) -> None:
        self._send(card, self.viewmodel.decide(card.view, decision, scope))

    def release(self, card: ApprovalCard) -> None:
        self._send(card, self.viewmodel.release(card.view))

    def _send(self, card: ApprovalCard, call: Coroutine[object, object, object]) -> None:
        if card.busy or self._clock() < self._guard_until:
            call.close()
            return
        card.busy = True
        # The card stays sensitive: an insensitive widget loses focus, and the next card would not get it.
        card.add_css_class("ac-approval-busy")
        _ = spawn_task(self._finish(card, call))

    async def _finish(self, card: ApprovalCard, call: Coroutine[object, object, object]) -> None:
        try:
            _ = await call
        except CoreCallError as error:
            if error.error.code in CLOSED_CODES:
                self._toast(translate("approvals.closed"))
                self._answer(card)
            else:
                self._retry(card, error.error.message)
        except Exception as error:  # noqa: BLE001 -- the request is still open, so the card stays
            self._retry(card, str(error))
        else:
            self._answer(card)

    def _answer(self, card: ApprovalCard) -> None:
        self._answered.add(card.view.id)
        self._sync()

    def _retry(self, card: ApprovalCard, error: str) -> None:
        card.busy = False
        card.remove_css_class("ac-approval-busy")
        self._toast(translate("approvals.failed").format(error=error))

    def _run_timer(self) -> None:
        if self.cards and self._timer is None:
            self._timer = GLib.timeout_add_seconds(_TICK_S, self._tick)
        elif not self.cards:
            self.stop()

    def _tick(self) -> bool:
        now = datetime.now(UTC)
        for card in self.cards:
            card.tick(now)
        return GLib.SOURCE_CONTINUE

    def stop(self) -> None:
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None
