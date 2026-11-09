"""The request waiting in the open tab, as a card over the bottom of its terminal.

The card floats, so showing it does not resize the terminal and the agent does not redraw. Its buttons
stay on screen whatever the agent sent: only the details scroll.

Nothing here takes the keyboard on its own: a new card must not catch what is being typed into a
terminal, so a person moves here with `next-attention`. The single-key bindings only work inside a card.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import ApprovalView, CommandDetail, EditDetail  # noqa: E402
from aconsole.contracts.common import ApprovalDecision, ApprovalId, ApprovalScope, RiskTag, TabId  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreCallError  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import CLOSED_CODES, ApprovalsViewModel, is_risky, safe_text, time_left  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import agent_name  # noqa: E402
from aconsole.ui.views.approvals.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Mapping

_DETAILS_HEIGHT: Final = 180
_NATURAL_CHARS: Final = 70
_BUTTON_GAP: Final = 6
_RISKY_ALLOW_MS: Final = 500
# Longer than the risky wait, so a key cannot do what the disabled button refuses.
_SHIFT_GUARD_S: Final = 0.6
_TICK_S: Final = 1


def _mono(text: str, *, full: bool = False) -> Gtk.Label:
    label = Gtk.Label(
        label=safe_text(text, limit=None) if full else safe_text(text),
        xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
        max_width_chars=_NATURAL_CHARS,
    )
    # Selectable labels take focus and would leave a stop between a card and its buttons.
    label.set_focusable(False)
    label.add_css_class("monospace")
    label.add_css_class("ac-approval-command")
    return label


def _expand_text(text: str, key: str) -> Gtk.Expander:
    expander = Gtk.Expander(label=translate(key))
    # Allocate long labels only when requested, keeping new approval cards cheap to display.
    _ = expander.connect("notify::expanded", _reveal_text, text)
    return expander


def _reveal_text(expander: Gtk.Expander, _property: GObject.ParamSpec, text: str) -> None:
    if expander.get_expanded() and expander.get_child() is None:
        expander.set_child(_mono(text, full=True))


def _readable(body: Gtk.Box, text: str) -> None:
    body.append(_mono(text))
    if safe_text(text) != safe_text(text, limit=None):
        body.append(_expand_text(text, "approvals.full_text"))


def place_at_bottom(overlay: Gtk.Overlay, child: Gtk.Widget, allocation: Gdk.Rectangle) -> bool:
    """Full width, as tall as the text needs at that width. GTK's default measures at the natural width."""
    width, height = overlay.get_width(), overlay.get_height()
    natural = child.measure(Gtk.Orientation.VERTICAL, width)[1]
    allocation.x, allocation.width = 0, width
    allocation.height = min(natural, height)
    allocation.y = height - allocation.height
    return True


class _WrapToEnd(Gtk.LayoutManager):
    """One row against the end, wrapping when it does not fit. A flow box cannot keep to the end and wrap."""

    @override
    def do_get_request_mode(self, _widget: Gtk.Widget) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    @override
    def do_measure(
        self, widget: Gtk.Widget, orientation: object, for_size: int,
    ) -> tuple[int, int, int, int]:
        sizes = _natural_sizes(widget)
        if orientation is Gtk.Orientation.HORIZONTAL:
            widths = [width for _child, width, _height in sizes]
            return (max(widths, default=0), sum(widths) + _BUTTON_GAP * (len(widths) - 1), -1, -1)
        rows = _rows(sizes, for_size if for_size >= 0 else sum(width for _child, width, _height in sizes))
        height = sum(max(item[2] for item in row) for row in rows) + _BUTTON_GAP * (len(rows) - 1)
        return (height, height, -1, -1)

    @override
    def do_allocate(self, widget: Gtk.Widget, width: int, _height: int, _baseline: int) -> None:
        top = 0
        for row in _rows(_natural_sizes(widget), width):
            left = width - sum(item[1] for item in row) - _BUTTON_GAP * (len(row) - 1)
            tallest = max(item[2] for item in row)
            for child, child_width, _child_height in row:
                place = Gdk.Rectangle()
                place.x, place.y, place.width, place.height = max(left, 0), top, min(child_width, width), tallest
                child.size_allocate(place, -1)
                left += child_width + _BUTTON_GAP
            top += tallest + _BUTTON_GAP


def _natural_sizes(widget: Gtk.Widget) -> list[tuple[Gtk.Widget, int, int]]:
    sizes: list[tuple[Gtk.Widget, int, int]] = []
    child = widget.get_first_child()
    while child is not None:
        width = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        sizes.append((child, width, child.measure(Gtk.Orientation.VERTICAL, width)[1]))
        child = child.get_next_sibling()
    return sizes


def _rows(
    sizes: list[tuple[Gtk.Widget, int, int]], width: int,
) -> list[list[tuple[Gtk.Widget, int, int]]]:
    rows: list[list[tuple[Gtk.Widget, int, int]]] = []
    used = 0
    for item in sizes:
        if rows and used + _BUTTON_GAP + item[1] <= width:
            rows[-1].append(item)
            used += _BUTTON_GAP + item[1]
        else:
            rows.append([item])
            used = item[1]
    return rows


class ApprovalCard(Gtk.Box):
    def __init__(self, view: ApprovalView, tab_title: str, panel: ApprovalPanel) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("ac-attention-card")
        self.add_css_class("ac-approval-body")
        self.view: ApprovalView = view
        self.busy: bool = False
        self._panel: ApprovalPanel = panel
        self._allow_timer: int | None = None
        self.append(self._heading(tab_title))
        risks = self._risks()
        if risks is not None:
            self.append(risks)
        self.details: Gtk.ScrolledWindow = self._details()
        self.append(self.details)
        cwd = Gtk.Label(
            label=translate("approvals.cwd").format(cwd=safe_text(view.cwd)),
            xalign=0, ellipsize=Pango.EllipsizeMode.START,
        )
        cwd.add_css_class("dim-label")
        cwd.add_css_class("caption")
        self.append(cwd)
        self.session_check: Gtk.CheckButton | None = None
        if view.allow_tab_session:
            self.session_check = Gtk.CheckButton(
                label=translate("approvals.allow_session"), tooltip_text=translate("approvals.allow_session_tip"),
            )
            self.append(self.session_check)
        self.deny_message: Gtk.Entry = Gtk.Entry(
            placeholder_text=translate("approvals.deny_message"),
            tooltip_text=translate("approvals.deny_message"), max_length=4000,
        )
        self.append(self.deny_message)
        self.append(self._buttons())
        self.tick(datetime.now(UTC))
        self.details.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [
                GObject.Value(GObject.TYPE_STRING, self.spoken()),
                GObject.Value(GObject.TYPE_STRING, translate("approvals.cwd").format(cwd=safe_text(view.cwd))),
            ],
        )

    def spoken(self) -> str:
        """What a screen reader says, risks first: they are why the person should listen to the rest."""
        parts: list[str] = [self.title.get_label()]
        if self.view.risk:
            parts.append(self._risk_text())
        parts.append(safe_text(self.view.summary))
        return ". ".join(parts)

    def show_allow(self) -> None:
        """A risky request cannot be allowed by the click that happened to land as it appeared."""
        if self._allow_timer is None and is_risky(self.view):
            self.allow_button.set_sensitive(False)
            self._allow_timer = GLib.timeout_add(_RISKY_ALLOW_MS, self._enable_allow)

    def _enable_allow(self) -> bool:
        self._allow_timer = None
        self.tick(datetime.now(UTC))
        return GLib.SOURCE_REMOVE

    def stop(self) -> None:
        if self._allow_timer is not None:
            _ = GLib.source_remove(self._allow_timer)
            self._allow_timer = None

    def _heading(self, tab_title: str) -> Gtk.Box:
        heading = Gtk.Box(spacing=12)
        self.title: Gtk.Label = Gtk.Label(
            label=self._title_text(), xalign=0, hexpand=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
            tooltip_text=tab_title or None,
        )
        self.title.add_css_class("heading")
        heading.append(self.title)
        self.timer: Gtk.Label = Gtk.Label()
        self.timer.add_css_class("dim-label")
        self.timer.add_css_class("numeric")
        heading.append(self.timer)
        return heading

    def _title_text(self) -> str:
        agent = agent_name(self.view.agent.value)
        detail = self.view.detail
        if isinstance(detail, CommandDetail):
            return translate("approvals.title.command").format(agent=agent)
        if isinstance(detail, EditDetail):
            return translate("approvals.title.edit", len(detail.files)).format(agent=agent, count=len(detail.files))
        return translate("approvals.title.tool").format(agent=agent, tool=safe_text(self.view.tool))

    def _risk_text(self) -> str:
        return " · ".join(
            translate("approvals.risk.protected")
            if isinstance(self.view.detail, EditDetail) and risk is RiskTag.DESTRUCTIVE
            else translate(f"approvals.risk.{risk.value}")
            for risk in sorted(self.view.risk)
        )

    def _risks(self) -> Gtk.Label | None:
        if not self.view.risk:
            return None
        label = Gtk.Label(
            label=f"⚠ {self._risk_text()}", xalign=0, halign=Gtk.Align.START,
            wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
        )
        label.add_css_class("ac-approval-risk")
        label.add_css_class("caption")
        return label

    def _details(self) -> Gtk.ScrolledWindow:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        view = self.view
        if not isinstance(view.detail, CommandDetail) or view.detail.command != view.summary:
            body.append(Gtk.Label(
                label=safe_text(view.summary), xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                max_width_chars=_NATURAL_CHARS,
            ))
        self._detail(body)
        # The card's first stop: a long command can be read from the keyboard, with the page keys.
        # An explicit role lets AT-SPI expose the supplied name of this focusable container.
        details = Gtk.ScrolledWindow(
            child=body, hscrollbar_policy=Gtk.PolicyType.NEVER, focusable=True,
            propagate_natural_height=True, max_content_height=_DETAILS_HEIGHT,
            accessible_role=Gtk.AccessibleRole.GROUP,
        )
        details.add_css_class("ac-approval-details")
        return details

    def _detail(self, body: Gtk.Box) -> None:
        detail = self.view.detail
        if isinstance(detail, CommandDetail):
            _readable(body, detail.command)
            if detail.args_preview:
                _readable(body, detail.args_preview)
        elif isinstance(detail, EditDetail):
            _readable(body, "\n".join(detail.files))
            if detail.diff_preview:
                body.append(_expand_text(detail.diff_preview, "approvals.diff_preview"))
            if detail.input_json is not None:
                body.append(_expand_text(detail.input_json, "approvals.full_input"))
        else:
            _readable(body, detail.json_preview)

    def _buttons(self) -> Gtk.Box:
        # Wraps rather than clips: the window can be narrow and the text large.
        row = Gtk.Box()
        row.set_layout_manager(_WrapToEnd())
        self.release_button: Gtk.Button = Gtk.Button(
            label=translate("approvals.release"), tooltip_text=translate("approvals.release_tip"),
        )
        self.release_button.add_css_class("flat")
        _ = self.release_button.connect("clicked", self._release)
        self.deny_button: Gtk.Button = Gtk.Button(label=translate("approvals.deny"))
        self.deny_button.add_css_class("destructive-action")
        _ = self.deny_button.connect("clicked", self._deny)
        self.allow_button: Gtk.Button = Gtk.Button(label=translate("approvals.allow"))
        # Never the default on a risky request: nothing should invite pressing it without reading.
        if not is_risky(self.view):
            self.allow_button.add_css_class("suggested-action")
        _ = self.allow_button.connect("clicked", self._allow)
        for button in (self.release_button, self.deny_button, self.allow_button):
            row.append(button)
        return row

    def tick(self, now: datetime) -> None:
        left = time_left(self.view.expires_at, now)
        self.timer.set_label(
            translate("approvals.time_up") if left is None else translate("approvals.left").format(time=left),
        )
        self.allow_button.set_sensitive(self.view.expires_at > now and self._allow_timer is None)
        if self.session_check is not None:
            self.session_check.set_sensitive(self.view.expires_at > now)

    def _allow(self, _button: Gtk.Button) -> None:
        for_session = self.session_check is not None and self.session_check.get_active()
        self._panel.decide(
            self, ApprovalDecision.ALLOW, ApprovalScope.TAB_SESSION if for_session else ApprovalScope.ONCE,
        )

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
        self._tab: TabId | None = None
        self.shown: ApprovalCard | None = None
        self.cards: list[ApprovalCard] = []
        self.stack: Gtk.Stack = Gtk.Stack(
            vhomogeneous=False, hhomogeneous=False, transition_type=Gtk.StackTransitionType.NONE,
        )
        self.append(self.stack)
        self.more_label: Gtk.Label = Gtk.Label(xalign=1, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.more_label.add_css_class("ac-approval-more")
        self.more_label.add_css_class("caption")
        self.more_label.add_css_class("dim-label")
        self.append(self.more_label)

    def update(
        self, approvals: tuple[ApprovalView, ...], titles: Mapping[str, str], tab: TabId | None,
    ) -> None:
        self._approvals = approvals
        self._titles = titles
        self._tab = tab
        self._sync()

    def _sync(self) -> None:
        open_requests = [item for item in self._approvals if item.id not in self._answered]
        wanted = [item for item in open_requests if item.tab_id == self._tab]
        wanted_ids = {item.id for item in wanted}
        focused = self.focused_index()
        focused_card = None if focused is None else self.cards[focused]
        shown_at = 0 if self.shown not in self.cards or self.shown is None else self.cards.index(self.shown)
        for card in [card for card in self.cards if card.view.id not in wanted_ids]:
            card.stop()
            self.stack.remove(card)
            self.cards.remove(card)
        known = {card.view.id for card in self.cards}
        for item in wanted:
            if item.id not in known:
                card = ApprovalCard(item, self._titles.get(str(item.tab_id), ""), self)
                self.cards.append(card)
                _ = self.stack.add_child(card)
        if self.shown not in self.cards:
            self._show(self.cards[min(shown_at, len(self.cards) - 1)] if self.cards else None)
        self._count_others(len(open_requests) - len(wanted))
        self.set_visible(bool(self.cards))
        self._run_timer()
        if focused is not None and focused_card not in self.cards:
            if self.shown is not None:
                _ = self.shown.details.grab_focus()
            else:
                self._left()

    def _show(self, card: ApprovalCard | None) -> None:
        """Change what the pointer or the key is over. The next press must not land on the new card."""
        self.shown = card
        if card is None:
            return
        self.stack.set_visible_child(card)
        self._guard_until = self._clock() + _SHIFT_GUARD_S
        card.show_allow()
        self.announce(card.spoken(), Gtk.AccessibleAnnouncementPriority.HIGH)

    def _count_others(self, elsewhere: int) -> None:
        parts: list[str] = []
        if len(self.cards) > 1:
            parts.append(translate("approvals.more_here", len(self.cards) - 1).format(count=len(self.cards) - 1))
        if elsewhere:
            parts.append(translate("approvals.more_elsewhere", elsewhere).format(count=elsewhere))
        self.more_label.set_label(" · ".join(parts))
        self.more_label.set_visible(bool(parts))

    def focused_index(self) -> int | None:
        root = self.get_root()
        widget = None if root is None else root.get_focus()
        while widget is not None:
            if isinstance(widget, ApprovalCard):
                return self.cards.index(widget) if widget in self.cards else None
            widget = widget.get_parent()
        return None

    def step(self, delta: int) -> bool:
        """Focus the next or previous card, or the shown one from outside. False when there is none."""
        if not self.cards or self.shown is None:
            return False
        index = self.focused_index()
        target = self.shown if index is None else self.cards[(index + delta) % len(self.cards)]
        if target is not self.shown:
            self._show(target)
        return target.details.grab_focus()

    def advance(self) -> bool:
        """This tab's requests in turn. False once the last is reached, when the next tab's turn comes."""
        index = self.focused_index()
        return self.step(1) if index is None or index + 1 < len(self.cards) else False

    def decide_focused(self, decision: ApprovalDecision) -> None:
        index = self.focused_index()
        if index is not None:
            self.decide(self.cards[index], decision)

    def open_focused(self) -> None:
        index = self.focused_index()
        if index is not None:
            self._open(self.cards[index].view.tab_id)

    def decide(self, card: ApprovalCard, decision: ApprovalDecision, scope: ApprovalScope = ApprovalScope.ONCE) -> None:
        now = datetime.now(UTC)
        card.tick(now)
        if decision == ApprovalDecision.ALLOW and card.view.expires_at <= now:
            self._toast(translate("approvals.closed"))
            return
        message = card.deny_message.get_text().strip() if decision is ApprovalDecision.DENY else ""
        self._send(card, self.viewmodel.decide(card.view, decision, scope, message=message or None))

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
                self._retry(card, str(error))
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
        for card in self.cards:
            card.stop()
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None
