"""Lanes are pure: every shape is checked without a window."""

from __future__ import annotations

from aconsole.ui.viewmodels.logic.git_lanes import LaneEdge, LaneLayout, tab_color


def test_a_line_stays_on_one_lane() -> None:
    lanes = LaneLayout()
    first = lanes.add("c", ("b",))
    second = lanes.add("b", ("a",))
    third = lanes.add("a", ())
    assert first.lane == second.lane == third.lane == 0
    assert first.below == (LaneEdge(0, 0, first.color),)
    assert second.above == (LaneEdge(0, 0, first.color),)
    assert third.below == ()


def test_base_keeps_lane_zero_even_when_branches_come_first() -> None:
    lanes = LaneLayout(base_oid="m")
    agent = lanes.add("x", ("m",), color=3)
    assert agent.lane == 1
    assert agent.color == 3
    assert agent.above == ()
    assert agent.below == (LaneEdge(1, 0, 0),)
    base = lanes.add("m", ("p",))
    assert base.lane == 0
    assert base.color == 0


def test_base_keeps_its_lane_when_a_branch_claimed_its_parent_first() -> None:
    lanes = LaneLayout(base_oid="m")
    agent = lanes.add("x", ("p",), color=3)
    assert agent.lane == 1
    base = lanes.add("m", ("p",))
    assert base.lane == 0
    assert LaneEdge(1, 0, 3) in base.below
    assert lanes.add("p", ()).lane == 0


def test_a_branch_joins_its_parent_lane_and_the_lane_frees() -> None:
    lanes = LaneLayout()
    _ = lanes.add("a", ("root",))
    other = lanes.add("b", ("root",))
    assert other.lane == 1
    assert {(edge.start, edge.end) for edge in other.below} == {(0, 0), (1, 0)}
    assert other.width == 2
    join = lanes.add("root", ())
    assert join.lane == 0
    assert {(edge.start, edge.end) for edge in join.above} == {(0, 0)}
    assert lanes.add("next", ()).lane == 0


def test_merge_opens_a_lane_for_the_second_parent() -> None:
    lanes = LaneLayout()
    merge = lanes.add("m", ("left", "right"))
    assert {(edge.start, edge.end) for edge in merge.below} == {(0, 0), (0, 1)}
    left = lanes.add("left", ("root",))
    assert (1, 1) in {(edge.start, edge.end) for edge in left.above}
    assert (1, 1) in {(edge.start, edge.end) for edge in left.below}


def test_a_merge_into_a_lane_that_also_passes_keeps_the_lane_whole() -> None:
    lanes = LaneLayout()
    _ = lanes.add("a", ("p",))
    merge = lanes.add("m", ("q", "p"))
    pairs = {(edge.start, edge.end) for edge in merge.below}
    assert (0, 0) in pairs
    assert (1, 0) in pairs


def test_snapshot_order_is_dashed_and_separate_from_git_edges() -> None:
    lanes = LaneLayout()
    newer = lanes.add("s2", ("head",), time_link="s1", color=2)
    solid = [edge for edge in newer.below if not edge.dashed]
    dashed = [edge for edge in newer.below if edge.dashed]
    assert len(solid) == 1
    assert len(dashed) == 1
    older = lanes.add("s1", ("head",), color=2)
    assert any(edge.dashed and edge.end == older.lane for edge in older.above)
    assert not any(edge.dashed for edge in older.below)


def test_pages_continue_the_same_edges() -> None:
    lanes = LaneLayout()
    last_of_page = lanes.add("b", ("a",))
    first_of_next = lanes.add("a", ())
    assert last_of_page.below[0].end == first_of_next.lane


def test_tab_color_is_stable_and_never_the_base() -> None:
    assert tab_color("01JB") == tab_color("01JB")
    assert tab_color("x") != 0
