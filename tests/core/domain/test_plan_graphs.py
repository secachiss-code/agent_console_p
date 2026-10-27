"""Random DAGs and one back edge, checked against cycle detection and Kahn order."""

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import NodeSpec, Plan

_AT = datetime(2026, 10, 27, tzinfo=UTC)
_EXAMPLES = 200
_MAX_NODES = 30


def _labels(order: list[int]) -> tuple[str, ...]:
    return tuple(f"n{index:02d}" for index in order)


def _forward_edges(draw: st.DrawFn, count: int) -> tuple[tuple[int, int], ...]:
    edges = [
        (src, dst)
        for src in range(count)
        for dst in range(src + 1, count)
        if draw(st.booleans())
    ]
    return tuple(edges)


def _reachable(count: int, edges: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    following: dict[int, list[int]] = {index: [] for index in range(count)}
    for src, dst in edges:
        following[src].append(dst)
    found: list[tuple[int, int]] = []
    for start in range(count):
        seen = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for nxt in following[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
                    found.append((start, nxt))
    return found


def _plan(names: tuple[str, ...], edges: tuple[tuple[int, int], ...]) -> Plan:
    plan = Plan()
    for name in names:
        _ = plan.add(NodeSpec(key=name, created_at=_AT))
    for src, dst in edges:
        plan.depend(names[dst], names[src])
    return plan


@st.composite
def _dags(draw: st.DrawFn) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...]]:
    count = draw(st.integers(min_value=1, max_value=_MAX_NODES))
    names = _labels(draw(st.permutations(list(range(count)))))
    return names, _forward_edges(draw, count)


@st.composite
def _with_back_edge(
    draw: st.DrawFn,
) -> tuple[tuple[str, ...], tuple[tuple[int, int], ...], tuple[int, int]]:
    count = draw(st.integers(min_value=2, max_value=_MAX_NODES))
    names = _labels(draw(st.permutations(list(range(count)))))
    edges = _forward_edges(draw, count)
    pairs = _reachable(count, edges)
    if not pairs:
        src = draw(st.integers(min_value=0, max_value=count - 2))
        dst = draw(st.integers(min_value=src + 1, max_value=count - 1))
        edges = (*edges, (src, dst))
        pairs = [(src, dst)]
    return names, edges, draw(st.sampled_from(pairs))


@given(_dags())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_plan_graphs_dag_kahn_respects_edges(
    graph: tuple[tuple[str, ...], tuple[tuple[int, int], ...]],
) -> None:
    names, edges = graph
    plan = _plan(names, edges)
    order = plan.kahn()
    assert order is not None
    assert set(order) == set(names)
    assert plan.cycle_path() is None
    place = {name: index for index, name in enumerate(order)}
    for src, dst in edges:
        assert place[names[src]] < place[names[dst]]


@given(_with_back_edge())
@settings(max_examples=_EXAMPLES, deadline=None)
def test_plan_graphs_back_edge_is_a_closed_path(
    graph: tuple[tuple[str, ...], tuple[tuple[int, int], ...], tuple[int, int]],
) -> None:
    names, edges, (src, dst) = graph
    plan = _plan(names, edges)
    known = {(names[left], names[right]) for left, right in edges}
    known.add((names[dst], names[src]))
    with pytest.raises(DomainError) as caught:
        plan.depend(names[src], names[dst])
    cycle = caught.value.extra
    assert caught.value.name == "PLAN_CYCLE"
    assert len(cycle) >= 2
    assert cycle[0] == cycle[-1]
    for index in range(len(cycle) - 1):
        assert (cycle[index], cycle[index + 1]) in known
    assert plan.cycle_path() is None
