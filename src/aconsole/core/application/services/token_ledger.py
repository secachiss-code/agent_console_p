"""Token spend for one tab. A task is one model stretch; the tab is the sum of its tasks.

The context window belongs to the open primary task. Spent tokens only grow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

_MODEL_CHARS = 64


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenReply:
    """One observation from a CLI. Buckets are this reply, except a running cost total."""

    model: str = ""
    fresh_input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    window_pct: float | None = None
    window_size: int | None = None
    window_tokens: int | None = None
    cost_total: float | None = None
    session: str = ""
    primary: bool = True
    key: str | None = None


@dataclass
class TokenTask:
    model: str
    session: str
    generation: int
    primary: bool
    fresh: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float = 0.0
    window_pct: float | None = None
    cost_mark: float | None = None
    open: bool = True

    def spent(self) -> int:
        return self.fresh + self.output + self.cache_read + self.cache_write


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenChange:
    spent: bool = False
    window: bool = False
    cost: float | None = None


@dataclass
class TokenBook:
    tasks: list[TokenTask] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    tab_cost_mark: float | None = None

    def note(
        self, reply: TokenReply, *, generation: int, session: str, scope: Literal["session", "tab"]
    ) -> TokenChange:
        """Fold one reply into the open task, or into a side model that does not own the window."""
        if reply.key is not None and reply.key in self.seen:
            return TokenChange(cost=self.cost())
        if reply.key is not None:
            self.seen.add(reply.key)
        bound = reply.session or session
        model = reply.model
        if reply.primary and not model.strip():
            current = next((task for task in reversed(self.tasks) if task.open and task.primary), None)
            model = "" if current is None else current.model
        if reply.primary:
            self._close_other(generation, bound, model)
            task = self._primary(generation, bound, model)
            window = _window(reply)
            changed_window = window is not None and window != task.window_pct
            if window is not None:
                task.window_pct = window
        else:
            task = self._side(generation, bound, reply.model)
            changed_window = False
        before = task.spent()
        task.fresh += max(0, reply.fresh_input)
        task.output += max(0, reply.output)
        task.cache_read += max(0, reply.cache_read)
        task.cache_write += max(0, reply.cache_write)
        if reply.cost_total is not None and reply.cost_total >= 0:
            _apply_cost(self, task, reply.cost_total, scope)
        return TokenChange(spent=task.spent() != before, window=changed_window, cost=self.cost())

    def close(self) -> bool:
        """The CLI conversation was cleared. The next reply opens a new task."""
        closed = False
        for task in self.tasks:
            if task.open:
                task.open = False
                closed = True
        return closed

    def spent(self) -> int:
        return sum(task.spent() for task in self.tasks)

    def cost(self) -> float:
        return round(sum(task.cost_usd for task in self.tasks), 4)

    def _close_other(self, generation: int, session: str, model: str) -> None:
        name = _model(model)
        for task in self.tasks:
            if (
                task.open
                and task.primary
                and (task.generation, task.session, task.model) != (generation, session, name)
            ):
                task.open = False

    def _primary(self, generation: int, session: str, model: str) -> TokenTask:
        name = _model(model)
        for task in self.tasks:
            if task.primary and task.generation == generation and task.session == session and task.model == name:
                task.open = True
                return task
        task = TokenTask(model=name, session=session, generation=generation, primary=True)
        self.tasks.append(task)
        return task

    def _side(self, generation: int, session: str, model: str) -> TokenTask:
        name = _model(model)
        for task in self.tasks:
            if not task.primary and task.generation == generation and task.session == session and task.model == name:
                return task
        task = TokenTask(model=name, session=session, generation=generation, primary=False, open=False)
        self.tasks.append(task)
        return task


def _model(model: str) -> str:
    name = model.strip() or "session"
    return name[:_MODEL_CHARS]


def _window(reply: TokenReply) -> float | None:
    if reply.window_pct is not None:
        return _pct(reply.window_pct)
    if reply.window_tokens is not None and reply.window_size:
        return _pct(100 * reply.window_tokens / reply.window_size)
    return None


def _pct(value: float) -> float:
    return min(100.0, max(0.0, value))


def _apply_cost(book: TokenBook, task: TokenTask, total: float, scope: Literal["session", "tab"]) -> None:
    if scope == "tab":
        mark = book.tab_cost_mark
        delta = total if mark is None or total < mark else total - mark
        book.tab_cost_mark = total
    else:
        mark = task.cost_mark
        delta = total if mark is None or total < mark else total - mark
        task.cost_mark = total
    if delta > 0:
        task.cost_usd = round(task.cost_usd + delta, 4)
