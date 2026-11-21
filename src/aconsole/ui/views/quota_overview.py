"""Daily token usage displayed in the limits dialog."""

from datetime import date, timedelta

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from aconsole.contracts.app import AccountView, AgentUsageHistoryView, ModelUsageView, UsageOverviewView  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import agent_name, format_tokens  # noqa: E402
from aconsole.ui.views.quota_account import detail  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

_PALETTE = (
    (0.18, 0.48, 0.85),
    (0.79, 0.37, 0.11),
    (0.18, 0.61, 0.45),
    (0.65, 0.38, 0.76),
    (0.71, 0.57, 0.12),
    (0.79, 0.32, 0.43),
)


def _tokens(model: ModelUsageView) -> int:
    return model.input_tokens + model.output_tokens + model.cache_read_tokens + model.cache_write_tokens


class UsageOverview(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("ac-quota-panel")
        self._usage: UsageOverviewView | None = None
        self._accounts: tuple[AccountView, ...] = ()
        self._render()

    def update(self, usage: UsageOverviewView | None, accounts: tuple[AccountView, ...]) -> None:
        if (self._usage, self._accounts) != (usage, accounts):
            self._usage, self._accounts = usage, accounts
            self._render()

    def _render(self) -> None:
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        title = Gtk.Label(label=translate("quota.overview"), xalign=0)
        title.add_css_class("heading")
        self.append(title)
        usage = self._usage
        histories = () if usage is None else tuple(history for history in usage.histories if history.days)
        if usage is None or not histories:
            self.append(detail(translate("quota.daily_unavailable")))
        else:
            self.append(detail(translate("quota.active_days").format(count=usage.active_days)))
            total = sum(_tokens(model) for history in histories for model in history.totals)
            self.append(detail(translate("quota.total_tokens").format(total=format_tokens(total))))
            for history in histories:
                self.append(_history_chart(history))
        charted = {history.agent for history in histories}
        for account in self._accounts:
            if account.agent not in charted and (histories or account.usage):
                self.append(_account_usage(account))


def _account_usage(account: AccountView) -> Gtk.Box:
    result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    title = Gtk.Label(label=agent_name(account.agent.value), xalign=0)
    title.add_css_class("heading")
    result.append(title)
    result.append(detail(translate("quota.daily_unavailable")))
    for model in account.usage:
        label = detail(f"{model.model} · {format_tokens(_tokens(model))}")
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        result.append(label)
    return result


def _history_chart(history: AgentUsageHistoryView) -> Gtk.Box:
    result = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    label = Gtk.Label(label=agent_name(history.agent.value), xalign=0)
    label.add_css_class("heading")
    result.append(label)
    colors = {model.model: _PALETTE[index % len(_PALETTE)] for index, model in enumerate(history.totals)}
    amounts = {day.day.date(): {model.model: _tokens(model) for model in day.models} for day in history.days}
    start, end = history.start_day.date(), history.end_day.date()
    chart = DailyChart(start, end, amounts, colors)
    result.append(chart)
    scale = Gtk.Box()
    scale.append(Gtk.Label(label=start.isoformat(), xalign=0, hexpand=True))
    scale.append(Gtk.Label(label=end.isoformat(), xalign=1))
    result.append(scale)
    for model in history.totals:
        red, green, blue = colors[model.model]
        swatch = f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"
        row = Gtk.Box(spacing=6)
        marker = Gtk.Label()
        marker.set_markup(f'<span foreground="{swatch}">●</span>')
        name = detail(f"{model.model} · {format_tokens(_tokens(model))}")
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        row.append(marker)
        row.append(name)
        result.append(row)
    return result


class DailyChart(Gtk.DrawingArea):
    def __init__(
        self,
        start: date,
        end: date,
        amounts: dict[date, dict[str, int]],
        colors: dict[str, tuple[float, float, float]],
    ) -> None:
        super().__init__(content_height=96, has_tooltip=True, hexpand=True)
        self._start: date = start
        self._length: int = max(1, (end - start).days + 1)
        self._amounts: dict[date, dict[str, int]] = amounts
        self._colors: dict[str, tuple[float, float, float]] = colors
        self._peak: int = max(1, *(sum(day.values()) for day in amounts.values()))
        self.set_draw_func(self._draw)
        _ = self.connect("query-tooltip", self._tooltip)

    def _draw(self, _area: Gtk.DrawingArea, ctx: cairo.Context[cairo.Surface], width: int, height: int) -> None:
        column = width / self._length
        for day, models in self._amounts.items():
            x = (day - self._start).days * column
            bottom = float(height)
            for model, amount in models.items():
                bar = height * amount / self._peak
                bottom -= bar
                ctx.set_source_rgb(*self._colors[model])
                ctx.rectangle(x, bottom, max(1, column - 2), bar)
                ctx.fill()

    def _tooltip(
        self,
        _widget: Gtk.Widget,
        x: int,
        _y: int,
        keyboard: bool,  # noqa: FBT001 -- GTK provides keyboard mode positionally
        tooltip: Gtk.Tooltip,
    ) -> bool:
        if keyboard or self.get_width() == 0:
            return False
        day = self._start + timedelta(days=x * self._length // self.get_width())
        models = self._amounts.get(day, {})
        values = [f"{model} · {format_tokens(amount)}" for model, amount in models.items()]
        tooltip.set_text("\n".join((day.isoformat(), *(values or [translate("quota.daily_unavailable")]))))
        return True
