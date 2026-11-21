"""Recorded pipeline attempts for a selected plan task."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from aconsole.contracts.app import PlanAttemptView  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

_STEPS = ("spec", "patch", "test", "verify")


class GraphSteps(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("ac-plan-details")
        self.title: Gtk.Label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("heading")
        self.append(self.title)
        self.choices: Gtk.DropDown = Gtk.DropDown(hexpand=True)
        _ = self.choices.connect("notify::selected", self._changed)
        self.append(self.choices)
        self.body: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll = Gtk.ScrolledWindow(child=self.body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(scroll)
        self._attempts: tuple[PlanAttemptView, ...] = ()
        self._loading: bool = False
        self._failed: bool = False

    def update(
        self,
        title: str,
        attempts: tuple[PlanAttemptView, ...],
        *,
        loading: bool = False,
        failed: bool = False,
    ) -> None:
        self.title.set_text(title)
        selected = self.choices.get_selected()
        previous = self._attempts[selected].number if selected < len(self._attempts) else None
        self._attempts = tuple(sorted(attempts, key=lambda attempt: -attempt.number))
        self._loading, self._failed = loading, failed
        labels = tuple(
            translate("graph.attempt_choice").format(
                number=attempt.number, status=translate(f"graph.attempt.{attempt.status}")
            )
            for attempt in self._attempts
        )
        self.choices.set_model(Gtk.StringList.new(labels))
        self.choices.set_visible(bool(labels))
        selected = next((index for index, attempt in enumerate(self._attempts) if attempt.number == previous), 0)
        self.choices.set_selected(selected)
        self._render()

    def _changed(self, _choice: Gtk.DropDown, _param: object) -> None:
        self._render()

    def _render(self) -> None:
        while (child := self.body.get_first_child()) is not None:
            self.body.remove(child)
        index = self.choices.get_selected()
        if index >= len(self._attempts):
            key = (
                "graph.attempt_unavailable"
                if self._failed
                else ("graph.attempt_loading" if self._loading else "graph.no_attempts")
            )
            self.body.append(_line(translate(key)))
            return
        attempt = self._attempts[index]
        completed = {step.step for step in attempt.steps}
        next_index = max((i + 1 for i, step in enumerate(_STEPS) if step in completed), default=0)
        for i, step in enumerate(_STEPS):
            if step in completed:
                status = "done"
            elif i < next_index:
                status = "skipped"
            elif i == next_index and attempt.status == "running":
                status = "current"
            else:
                status = "pending"
            self.body.append(_line(f"{step} · {translate(f'graph.step.{status}')}"))
        if attempt.verdict:
            self.body.append(_line(attempt.verdict))


def _line(text: str) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=0, selectable=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
