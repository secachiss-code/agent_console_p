"""Deterministic local ranking of palette items."""

from collections.abc import Sequence

from rapidfuzz.fuzz import WRatio

from aconsole.contracts.app import PaletteItem

_MAX_SCORE = 100.0


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _match(item: PaletteItem, needle: str, title: str, cutoff: float) -> tuple[int, float] | None:
    aliases = tuple(_normalize(alias) for alias in item.aliases)
    if needle in aliases:
        return 0, _MAX_SCORE
    if any(alias.startswith(needle) for alias in aliases):
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
    ranked: list[tuple[int, float, float, str, str, PaletteItem]] = []
    for item in items:
        title = _normalize(item.title)
        if not needle:
            if item.score_hint > 0:
                ranked.append((0, 0, -item.score_hint, title, item.id, item))
            continue
        match = _match(item, needle, title, score_cutoff)
        if match is None:
            continue
        match_class, score = match
        ranked.append((match_class, -score, -item.score_hint, title, item.id, item))
    ranked.sort(key=lambda entry: entry[:5])
    return tuple(entry[5] for entry in ranked[:limit])
