"""Three-way vault merge: 25 table rows, then disjoint edits commute."""

from dataclasses import replace

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from aconsole.core.domain.vault_sync import FIELD_OWNER, Note, Owner, Owners, merge, resolve_ref

BASE = Note(title="Task", objective="Ship it", status="pending")


def _owners(**extra: object) -> Owners:
    known = extra.get("known")
    edges = extra.get("edges")
    blockers = extra.get("blockers")
    return Owners(
        node_key="N-03",
        known_keys=known if isinstance(known, frozenset) else frozenset({"N-01", "N-02", "N-03"}),
        edges=edges if isinstance(edges, tuple) else (),
        blocker_status=blockers if isinstance(blockers, tuple) else (),
        leased_by_other=extra.get("leased") is True,
        release_confirmed=extra.get("confirmed") is True,
    )


def _row(
    name: str,
    user: Note,
    app: Note,
    owners: Owners,
    apply: Note,
    restore: tuple[str, ...] = (),
    conflicts: tuple[str, ...] = (),
    base: Note = BASE,
) -> tuple[str, Note, Note, Note, Owners, Note, tuple[str, ...], tuple[str, ...]]:
    return (name, base, user, app, owners, apply, restore, conflicts)


def _cases() -> tuple[tuple[str, Note, Note, Owners, Note, tuple[str, ...], tuple[str, ...]], ...]:
    own = _owners()
    leased = _owners(leased=True)
    confirmed = _owners(leased=True, confirmed=True)
    blocked = _owners(blockers=(("N-01", "pending"),))
    clear = _owners(blockers=(("N-01", "done"),))
    cycle = _owners(edges=(("N-01", "N-03"),))
    return (
        _row("unchanged", BASE, BASE, own, BASE),
        _row("user title", replace(BASE, title="Renamed"), BASE, own, replace(BASE, title="Renamed")),
        _row("user objective", replace(BASE, objective="Now"), BASE, own, replace(BASE, objective="Now")),
        _row(
            "user acceptance",
            replace(BASE, acceptance=("tests pass",)),
            BASE,
            own,
            replace(BASE, acceptance=("tests pass",)),
        ),
        _row(
            "user review flag",
            replace(BASE, review_required=True),
            BASE,
            own,
            replace(BASE, review_required=True),
        ),
        _row("app tags", BASE, replace(BASE, tags=("aconsole/task",)), own, replace(BASE, tags=("aconsole/task",))),
        _row("app lease", BASE, replace(BASE, lease="codex"), own, replace(BASE, lease="codex")),
        _row("app updated", BASE, replace(BASE, updated="t"), own, replace(BASE, updated="t")),
        _row("user cannot edit tags", replace(BASE, tags=("x",)), BASE, own, BASE, ("tags",)),
        _row("app cannot edit title", BASE, replace(BASE, title="App"), own, BASE),
        _row(
            "both title, user wins",
            replace(BASE, title="User"),
            replace(BASE, title="App"),
            own,
            replace(BASE, title="User"),
            conflicts=("title",),
        ),
        _row(
            "both objective, user wins",
            replace(BASE, objective="User"),
            replace(BASE, objective="App"),
            own,
            replace(BASE, objective="User"),
            conflicts=("objective",),
        ),
        _row(
            "pending to blocked",
            replace(BASE, status="blocked"),
            BASE,
            own,
            replace(BASE, status="blocked"),
        ),
        _row("pending to done is illegal", replace(BASE, status="done"), BASE, own, BASE, ("status",)),
        _row(
            "pending to cancelled",
            replace(BASE, status="cancelled"),
            BASE,
            own,
            replace(BASE, status="cancelled"),
        ),
        _row(
            "reopen done",
            replace(BASE, status="pending"),
            replace(BASE, status="done"),
            own,
            replace(BASE, status="pending"),
            base=replace(BASE, status="done"),
        ),
        _row(
            "cancel while working",
            replace(BASE, status="cancelled"),
            replace(BASE, status="in_progress"),
            own,
            replace(BASE, status="cancelled"),
            base=replace(BASE, status="in_progress"),
        ),
        _row(
            "claim while blocked",
            replace(BASE, status="in_progress", depends_on=("N-01",)),
            BASE,
            blocked,
            replace(BASE, depends_on=("N-01",)),
            ("status",),
        ),
        _row(
            "claim when ready",
            replace(BASE, status="in_progress"),
            BASE,
            own,
            replace(BASE, status="in_progress"),
        ),
        _row(
            "done while a blocker is open",
            replace(BASE, status="done", depends_on=("N-01",)),
            BASE,
            blocked,
            replace(BASE, depends_on=("N-01",)),
            ("status",),
        ),
        _row(
            "human finishes verifying",
            replace(BASE, status="done", depends_on=("N-01",)),
            replace(BASE, status="verifying", depends_on=("N-01",)),
            clear,
            replace(BASE, status="done", depends_on=("N-01",)),
            base=replace(BASE, status="verifying", depends_on=("N-01",)),
        ),
        _row(
            "leased release needs confirmation",
            replace(BASE, status="pending"),
            replace(BASE, status="in_progress"),
            leased,
            replace(BASE, status="in_progress"),
            ("status",),
            base=replace(BASE, status="in_progress"),
        ),
        _row(
            "leased release confirmed",
            replace(BASE, status="pending"),
            replace(BASE, status="in_progress"),
            confirmed,
            replace(BASE, status="pending"),
            base=replace(BASE, status="in_progress"),
        ),
        _row(
            "leased cancel needs confirmation",
            replace(BASE, status="cancelled"),
            BASE,
            leased,
            BASE,
            ("status",),
        ),
        _row(
            "wikilink dependency",
            replace(BASE, depends_on=("[[N-01-domain-model]]",)),
            BASE,
            own,
            replace(BASE, depends_on=("N-01",)),
        ),
        _row(
            "unknown dependency",
            replace(BASE, depends_on=("N-99",)),
            BASE,
            own,
            BASE,
            ("depends_on",),
        ),
        _row(
            "cyclic dependency",
            replace(BASE, depends_on=("N-01",)),
            BASE,
            cycle,
            BASE,
            ("depends_on",),
        ),
        _row("user paths", replace(BASE, paths=("src/**",)), BASE, own, replace(BASE, paths=("src/**",))),
        _row("user owner", replace(BASE, owner_agent="codex"), BASE, own, replace(BASE, owner_agent="codex")),
        _row(
            "both status, user legal",
            replace(BASE, status="blocked"),
            replace(BASE, status="cancelled"),
            own,
            replace(BASE, status="blocked"),
            conflicts=("status",),
        ),
        _row(
            "both status, core wins when illegal",
            replace(BASE, status="done"),
            replace(BASE, status="blocked"),
            own,
            replace(BASE, status="blocked"),
            ("status",),
            ("status",),
        ),
        _row(
            "both paths, user wins",
            replace(BASE, paths=("a",)),
            replace(BASE, paths=("b",)),
            own,
            replace(BASE, paths=("a",)),
            conflicts=("paths",),
        ),
        _row(
            "illegal status keeps the title",
            replace(BASE, status="done", title="Kept"),
            BASE,
            own,
            replace(BASE, title="Kept"),
            ("status",),
        ),
        _row(
            "disjoint title and tags",
            replace(BASE, title="Kept"),
            replace(BASE, tags=("aconsole/task",)),
            own,
            replace(BASE, title="Kept", tags=("aconsole/task",)),
        ),
        _row(
            "working to verifying needs evidence",
            replace(BASE, status="verifying"),
            replace(BASE, status="in_progress"),
            own,
            replace(BASE, status="in_progress"),
            ("status",),
            base=replace(BASE, status="in_progress"),
        ),
        _row(
            "app depends_on",
            BASE,
            replace(BASE, depends_on=("N-02",)),
            own,
            replace(BASE, depends_on=("N-02",)),
        ),
    )


def test_merge_table_covers_at_least_25_rows() -> None:
    rows = _cases()
    assert len(rows) >= 25
    for name, base, user, app, owners, apply, restore, conflicts in rows:
        got = merge(base, user, app, owners)
        assert got.apply == apply, name
        assert got.restore_fields == restore, name
        assert got.conflicts == conflicts, name


def test_reopen_uses_the_base_status() -> None:
    base = replace(BASE, status="done")
    got = merge(base, replace(base, status="pending"), base, _owners())
    assert got.apply.status == "pending"
    assert got.restore_fields == ()


def test_cancel_while_working_uses_the_base_status() -> None:
    base = replace(BASE, status="in_progress")
    got = merge(base, replace(base, status="cancelled"), base, _owners())
    assert got.apply.status == "cancelled"


def test_resolve_wikilink_and_plain_key() -> None:
    assert resolve_ref("[[N-01-domain-model]]") == "N-01"
    assert resolve_ref("N-03") == "N-03"
    assert resolve_ref("not a key") is None


def test_field_owners_match_the_vault_contract() -> None:
    assert FIELD_OWNER["title"] is Owner.USER
    assert FIELD_OWNER["status"] is Owner.SHARED
    assert FIELD_OWNER["lease"] is Owner.APP


_EDITABLE = ("title", "objective", "paths", "owner_agent", "acceptance")


def _paint(note: Note, field: str, marker: str) -> Note:
    if field == "title":
        return replace(note, title=marker)
    if field == "objective":
        return replace(note, objective=marker)
    if field == "paths":
        return replace(note, paths=(marker,))
    if field == "owner_agent":
        return replace(note, owner_agent=marker)
    return replace(note, acceptance=(marker,))


@given(
    st.lists(st.sampled_from(_EDITABLE), max_size=3, unique=True),
    st.lists(st.sampled_from(_EDITABLE), max_size=3, unique=True),
)
@settings(max_examples=40)
def test_disjoint_user_edits_commute(left: list[str], right: list[str]) -> None:
    assume(not set(left) & set(right))
    owners = _owners()

    def _apply(note: Note, fields: list[str], marker: str) -> Note:
        current = note
        for field in fields:
            current = _paint(current, field, marker)
        return merge(note, current, note, owners).apply

    after_left = _apply(BASE, left, "left")
    left_then_right = _apply(after_left, right, "right")
    after_right = _apply(BASE, right, "right")
    right_then_left = _apply(after_right, left, "left")
    assert left_then_right == right_then_left
