"""Deterministic local ranking of palette items."""

from collections.abc import Sequence

import msgspec
from rapidfuzz.fuzz import WRatio

from aconsole.contracts.app import PaletteItem

_MAX_SCORE = 100.0


class _Ranked(msgspec.Struct, frozen=True, order=True, gc=False):
    # Private, transient records only point to scalars and an immutable PaletteItem.
    # They cannot form cycles or escape this call, so tracking them adds GC pressure
    # without collecting anything. The process-wide collector remains enabled.
    match_class: int
    score: float
    hint: float
    title: str
    item_id: str
    order: int  # stable ties stop comparison before the non-orderable item
    item: PaletteItem


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _match(item: PaletteItem, needle: str, title: str, cutoff: float) -> tuple[int, float] | None:
    prefix = False
    for raw in item.aliases:
        alias = _normalize(raw)
        if alias == needle:
            return 0, _MAX_SCORE
        prefix |= alias.startswith(needle)
    if prefix:
        return 1, _MAX_SCORE
    score = WRatio(needle, title, score_cutoff=cutoff)
    if score >= cutoff and score > 0:
        return 2, score
    texts = (*item.keywords, item.subtitle or "")
    score = max((WRatio(needle, _normalize(text), score_cutoff=cutoff) for text in texts), default=0.0)
    if score < cutoff or score <= 0:
        return None
    return 3, score


def rank_items(
    items: Sequence[PaletteItem],
    query: str,
    *,
    score_cutoff: float = 60.0,
    limit: int | None = None,
) -> tuple[PaletteItem, ...]:
    """Return matching items in relevance order without modifying the input."""
    if not 0 <= score_cutoff <= _MAX_SCORE:
        message = "score_cutoff must be between 0 and 100"
        raise ValueError(message)
    if limit is not None and limit < 0:
        message = "limit must be nonnegative"
        raise ValueError(message)
    if limit == 0:
        return ()
    needle = _normalize(query)
    ranked: list[_Ranked] = []
    for order, item in enumerate(items):
        title = _normalize(item.title)
        if not needle:
            if item.score_hint > 0:
                ranked.append(_Ranked(0, 0.0, -item.score_hint, title, item.id, order, item))
            continue
        match = _match(item, needle, title, score_cutoff)
        if match is None:
            continue
        match_class, score = match
        ranked.append(_Ranked(match_class, -score, -item.score_hint, title, item.id, order, item))
    ranked.sort()
    return tuple(entry.item for entry in ranked[:limit])
