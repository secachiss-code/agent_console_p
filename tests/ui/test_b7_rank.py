"""Independent ranking policy coverage for the command palette."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.app import PaletteItem
from aconsole.contracts.common import PaletteKind

if TYPE_CHECKING:
    from collections.abc import Sequence


def _item(
    item_id: str,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    keywords: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
    score_hint: float = 0.0,
    enabled: bool = True,
) -> PaletteItem:
    return PaletteItem(
        id=item_id,
        kind=PaletteKind.COMMAND,
        title=title if title is not None else item_id,
        subtitle=subtitle,
        keywords=keywords,
        aliases=aliases,
        score_hint=score_hint,
        enabled=enabled,
        disabled_reason=None,
        argument=None,
        shortcut=None,
    )


def _rank_cases() -> list[pytest.ParamSpecArgs]:
    cases: list[pytest.ParamSpecArgs] = []

    # Exact aliases outrank prefix aliases and text matches. Hint breaks ties
    # between exact aliases. Ten separately parameterized inputs exercise this.
    for index in range(10):
        query = f"alias{index:02}"
        items = (
            _item("prefix", aliases=(query + "-extra",), score_hint=99),
            _item("exact-low", aliases=(query,), score_hint=1),
            _item("text", title=query, score_hint=100),
            _item("exact-high", aliases=(query,), score_hint=8),
        )
        cases.append(pytest.param(items, query, ("exact-high", "exact-low", "prefix")))

    # Prefix aliases outrank title WRatio matches. Score hint then title/id are
    # the tie breakers within this match class. Eight more distinct inputs.
    for index in range(10, 18):
        query = f"pre{index:02}"
        items = (
            _item("text", title=query, score_hint=100),
            _item("prefix-low", aliases=(query + "-one",), score_hint=2),
            _item("prefix-high", aliases=(query + "-two",), score_hint=7),
            _item("unrelated", title="Completely unrelated"),
        )
        cases.append(pytest.param(items, query, ("prefix-high", "prefix-low", "text")))

    # Title WRatio matches outrank subtitle/keyword WRatio matches. Six inputs.
    for index in range(18, 24):
        query = f"title{index:02}"
        items = (
            _item("keyword", keywords=(query,), score_hint=100),
            _item("title-low", title=query, score_hint=1),
            _item("subtitle", title="unrelated text", subtitle=query, score_hint=100),
            _item("title-high", title=query, score_hint=6),
        )
        cases.append(pytest.param(items, query, ("title-high", "title-low", "keyword")))

    # Subtitle and keyword matches share one class; hint breaks their tie. Six.
    for index in range(24, 30):
        query = f"meta{index:02}"
        items = (
            _item("keyword-low", keywords=(query,), score_hint=1),
            _item("subtitle-high", subtitle=query, score_hint=8),
            _item("keyword-high", keywords=(query,), score_hint=5),
            _item("unrelated", title="Unrelated item"),
        )
        cases.append(pytest.param(items, query, ("subtitle-high", "keyword-high", "keyword-low")))

    return cases


@pytest.mark.parametrize(("items", "query", "expected_ids"), _rank_cases())
def test_b7_rank_order(
    items: Sequence[PaletteItem], query: str, expected_ids: tuple[str, ...]
) -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    assert tuple(item.id for item in rank_items(items, query)[:3]) == expected_ids


def test_rank_normalizes_query_and_alias_whitespace_and_case() -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    exact = _item("exact", aliases=("  Open   Project  ",))
    prefix = _item("prefix", aliases=("open project settings",), score_hint=100)
    assert tuple(item.id for item in rank_items((prefix, exact), "  OPEN  project ")) == (
        "exact",
        "prefix",
    )


@pytest.mark.parametrize(("limit", "expected_ids"), [(None, ("two", "one")), (0, ())])
def test_rank_limit_applies_after_ordering(
    limit: int | None, expected_ids: tuple[str, ...]
) -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    items = (_item("two", aliases=("go",), score_hint=2), _item("one", aliases=("go",), score_hint=1))
    assert tuple(item.id for item in rank_items(items, "go", limit=limit)) == expected_ids


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"limit": -1}, id="negative-limit"),
        pytest.param({"score_cutoff": -0.01}, id="cutoff-below-range"),
        pytest.param({"score_cutoff": 100.01}, id="cutoff-above-range"),
    ],
)
def test_rank_rejects_out_of_range_options(kwargs: dict[str, float | int]) -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    with pytest.raises(ValueError, match=r".+"):
        rank_items((_item("one", aliases=("go",)),), "go", **kwargs)


def test_rank_score_cutoff_includes_boundary_and_excludes_below_it() -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    exact = _item("exact", aliases=("switch",))
    fuzzy = _item("fuzzy", title="switch")
    assert tuple(item.id for item in rank_items((fuzzy, exact), "switch", score_cutoff=100)) == (
        "exact",
        "fuzzy",
    )


def test_rank_empty_query_shows_positive_hints_in_hint_order_including_disabled() -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    items = (
        _item("zero", score_hint=0),
        _item("disabled", score_hint=3, enabled=False),
        _item("recent", score_hint=8),
    )
    assert tuple(item.id for item in rank_items(items, "")) == ("recent", "disabled")


def test_rank_tie_breaks_by_normalized_title_then_id() -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    items = (
        _item("z-id", title=" Beta ", aliases=("go",), score_hint=4),
        _item("b-id", title="alpha", aliases=("go",), score_hint=4),
        _item("a-id", title="ALPHA", aliases=("go",), score_hint=4),
    )
    assert tuple(item.id for item in rank_items(items, "go")) == ("a-id", "b-id", "z-id")


def test_rank_does_not_mutate_input() -> None:
    from aconsole.ui.viewmodels.logic.rank import rank_items

    items = [_item("later", aliases=("go",), score_hint=1), _item("first", aliases=("go",), score_hint=2)]
    original = items.copy()
    rank_items(items, "go")
    assert items == original


def test_rank_2000_under_16ms() -> None:
    from time import perf_counter

    from aconsole.ui.viewmodels.logic.rank import rank_items

    items = tuple(
        _item(
            f"command-{index:04}",
            title=f"Switch workspace {index:04}",
            subtitle="workspace navigation",
            keywords=("switch", "workspace"),
            aliases=(f"switch-{index:04}",),
            score_hint=float(index % 17),
        )
        for index in range(2000)
    )
    started = perf_counter()
    ranked = rank_items(items, "swi")
    elapsed = perf_counter() - started

    assert ranked
    assert elapsed < 0.016
