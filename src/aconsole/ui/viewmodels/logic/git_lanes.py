"""Lanes of a commit graph, row by row. Children come first; nothing here draws.

`LaneLayout` keeps its state between pages, so the next page continues the edges of the last one. A lane that still
expects a commit when the loaded rows end simply runs on: the row below it has not arrived yet.
"""

from dataclasses import dataclass, field
from typing import Final

BASE_LANE: Final = 0
BASE_COLOR: Final = 0
COLORS: Final = 8
_ROUND_ROBIN_START: Final = 1


@dataclass(frozen=True, slots=True)
class LaneEdge:
    """One line across half a row, from `start` lane to `end` lane."""

    start: int
    end: int
    color: int
    dashed: bool = False


@dataclass(frozen=True, slots=True)
class RowLayout:
    lane: int
    color: int
    above: tuple[LaneEdge, ...]
    below: tuple[LaneEdge, ...]

    @property
    def width(self) -> int:
        lanes = [self.lane]
        for edge in (*self.above, *self.below):
            lanes.extend((edge.start, edge.end))
        return max(lanes) + 1


@dataclass(frozen=True, slots=True)
class _Lane:
    oid: str
    color: int
    dashed: bool
    started: bool = True


def tab_color(tab_id: str) -> int:
    """The same tab has the same color in every session. Color 0 stays the base branch's."""
    return _ROUND_ROBIN_START + sum(tab_id.encode()) % (COLORS - _ROUND_ROBIN_START)


@dataclass(slots=True)
class LaneLayout:
    """Feed rows newest first. `base_oid` keeps the base branch on lane 0 whenever its tip shows up."""

    base_oid: str | None = None
    _lanes: list[_Lane | None] = field(default_factory=list[_Lane | None])
    _next_color: int = _ROUND_ROBIN_START

    def __post_init__(self) -> None:
        if self.base_oid is not None:
            self._lanes.append(_Lane(self.base_oid, BASE_COLOR, dashed=False, started=False))

    def add(
        self,
        oid: str,
        parents: tuple[str, ...],
        *,
        color: int | None = None,
        time_link: str | None = None,
    ) -> RowLayout:
        """One row. `time_link` is the previous snapshot: drawn dashed, because git does not know this edge."""
        lane = self._arriving(oid)
        taken = lane if lane is not None else self._free()
        arrived = self._lane(taken)
        chosen = arrived.color if arrived is not None else self._new_color(color)
        above = self._above(oid, taken)
        self._clear(oid)
        self._put(taken, None)
        below = self._below(taken, chosen, parents, time_link)
        self._trim()
        return RowLayout(lane=taken, color=chosen, above=above, below=below)

    def _arriving(self, oid: str) -> int | None:
        solid = next(
            (at for at, item in enumerate(self._lanes) if item is not None and item.oid == oid and not item.dashed),
            None,
        )
        if solid is not None:
            return solid
        return next((at for at, item in enumerate(self._lanes) if item is not None and item.oid == oid), None)

    def _lane(self, at: int) -> _Lane | None:
        return self._lanes[at] if at < len(self._lanes) else None

    def _free(self) -> int:
        for at, item in enumerate(self._lanes):
            if item is None and not (at == BASE_LANE and self.base_oid is not None):
                return at
        self._lanes.append(None)
        return len(self._lanes) - 1

    def _new_color(self, wanted: int | None) -> int:
        if wanted is not None:
            return wanted
        chosen = self._next_color
        self._next_color = _ROUND_ROBIN_START + (self._next_color % (COLORS - _ROUND_ROBIN_START))
        return chosen

    def _above(self, oid: str, taken: int) -> tuple[LaneEdge, ...]:
        edges: list[LaneEdge] = []
        for at, item in enumerate(self._lanes):
            if item is None or not item.started:
                continue
            end = taken if item.oid == oid else at
            edges.append(LaneEdge(at, end, item.color, item.dashed))
        return tuple(edges)

    def _clear(self, oid: str) -> None:
        for at, item in enumerate(self._lanes):
            if item is not None and item.oid == oid:
                self._lanes[at] = None

    def _put(self, at: int, lane: _Lane | None) -> None:
        while len(self._lanes) <= at:
            self._lanes.append(None)
        self._lanes[at] = lane

    def _below(
        self, taken: int, color: int, parents: tuple[str, ...], time_link: str | None,
    ) -> tuple[LaneEdge, ...]:
        edges = self._keep_base_on_its_lane(taken, parents)
        edges.extend(
            LaneEdge(at, at, item.color, item.dashed)
            for at, item in enumerate(self._lanes)
            if item is not None and item.started and at != taken
        )
        wanted = [(parent, False) for parent in parents]
        if time_link is not None:
            wanted.append((time_link, True))
        for number, (parent, dashed) in enumerate(wanted):
            existing = next(
                (
                    at
                    for at, item in enumerate(self._lanes)
                    if item is not None and item.oid == parent and item.dashed == dashed
                ),
                None,
            )
            if existing is not None:
                self._start(existing)
                held = self._lanes[existing]
                edges.append(LaneEdge(taken, existing, color if held is None else held.color, dashed))
                continue
            if number == 0 and self._lane(taken) is None:
                self._put(taken, _Lane(parent, color, dashed))
                edges.append(LaneEdge(taken, taken, color, dashed))
                continue
            free = self._free()
            branch_color = color if dashed else self._new_color(None)
            self._put(free, _Lane(parent, branch_color, dashed))
            edges.append(LaneEdge(taken, free, branch_color, dashed))
        return tuple(edges)

    def _keep_base_on_its_lane(self, taken: int, parents: tuple[str, ...]) -> list[LaneEdge]:
        """A branch may have claimed the base's parent first. It joins here, so lane 0 stays the base."""
        if taken != BASE_LANE or self.base_oid is None or not parents:
            return []
        joined: list[LaneEdge] = []
        for at, item in enumerate(self._lanes):
            if item is not None and item.oid == parents[0] and not item.dashed and at != taken:
                joined.append(LaneEdge(at, taken, item.color))
                self._lanes[at] = None
        return joined

    def _start(self, at: int) -> None:
        held = self._lanes[at]
        if held is not None and not held.started:
            self._lanes[at] = _Lane(held.oid, held.color, held.dashed, started=True)

    def _trim(self) -> None:
        while self._lanes and self._lanes[-1] is None and len(self._lanes) > 1:
            _ = self._lanes.pop()
