"""Pure three-way merge of a vault note. Disk wins user fields; illegal shared edits are restored."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import NodeSpec, NodeStatus, Plan, human_transition_issue

_KEY = re.compile(r"[A-Z]{1,4}-\d{1,4}")
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class Owner(StrEnum):
    USER = "user"
    SHARED = "shared"
    APP = "app"


FIELD_OWNER: dict[str, Owner] = {
    "title": Owner.USER,
    "objective": Owner.USER,
    "acceptance": Owner.USER,
    "review_required": Owner.USER,
    "status": Owner.SHARED,
    "owner_agent": Owner.SHARED,
    "depends_on": Owner.SHARED,
    "paths": Owner.SHARED,
    "tags": Owner.APP,
    "lease": Owner.APP,
    "updated": Owner.APP,
}


@dataclass(frozen=True, kw_only=True)
class Note:
    status: str = "pending"
    owner_agent: str = ""
    depends_on: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    review_required: bool = False
    tags: tuple[str, ...] = ()
    lease: str = ""
    updated: str = ""
    title: str = ""
    objective: str = ""


@dataclass(frozen=True, kw_only=True)
class Owners:
    """Who owns each field, plus the graph facts needed to validate one note."""

    node_key: str = "N-03"
    known_keys: frozenset[str] = frozenset({"N-03"})
    edges: tuple[tuple[str, str], ...] = ()
    blocker_status: tuple[tuple[str, str], ...] = ()
    leased_by_other: bool = False
    release_confirmed: bool = False


@dataclass(frozen=True, kw_only=True)
class MergeDecision:
    apply: Note
    restore_fields: tuple[str, ...]
    conflicts: tuple[str, ...]


def resolve_ref(raw: str) -> str | None:
    """Turn `N-01` or `[[N-01-slug]]` into a node key."""
    text = raw.strip().strip('"').strip("'")
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2].split("|", 1)[0]
    matched = _KEY.match(text)
    if matched is None or not text.startswith(matched.group(0)):
        return None
    rest = text[matched.end() :]
    if rest != "" and not rest.startswith("-"):
        return None
    return matched.group(0)


@dataclass(frozen=True, kw_only=True)
class _Sides:
    base: Note
    user: Note
    app: Note


def merge(base: Note, user: Note, app: Note, owners: Owners) -> MergeDecision:
    """Merge the last synced note, the file on disk, and the note the core wants to write."""
    sides = _Sides(base=base, user=user, app=app)
    values = _as_dict(base)
    restore: list[str] = []
    conflicts: list[str] = []
    accepted_depends = _merge_depends(sides, owners, values, restore, conflicts)
    for field, owner in FIELD_OWNER.items():
        if field == "depends_on":
            continue
        decision = _field(field, owner, sides, owners, accepted_depends)
        if decision.value is not _MISSING:
            values[field] = decision.value
        if decision.restore:
            restore.append(field)
        if decision.conflict:
            conflicts.append(field)
    return MergeDecision(
        apply=_from_dict(values),
        restore_fields=tuple(sorted(restore)),
        conflicts=tuple(sorted(set(conflicts))),
    )


def _merge_depends(
    sides: _Sides,
    owners: Owners,
    values: dict[str, object],
    restore: list[str],
    conflicts: list[str],
) -> tuple[str, ...]:
    user_changed = _changed(sides.base, sides.user, "depends_on")
    app_changed = _changed(sides.base, sides.app, "depends_on")
    accepted = sides.base.depends_on
    if user_changed and app_changed:
        conflicts.append("depends_on")
    if not user_changed and app_changed:
        values["depends_on"] = _join(sides.app.depends_on)
        return sides.app.depends_on
    if not user_changed:
        return accepted
    resolved, issue = _depends_issue(sides.user.depends_on, owners)
    if issue is None and resolved is not None:
        values["depends_on"] = _join(resolved)
        return resolved
    restore.append("depends_on")
    if app_changed:
        values["depends_on"] = _join(sides.app.depends_on)
        return sides.app.depends_on
    return accepted


_MISSING = object()


@dataclass(frozen=True, kw_only=True)
class _FieldDecision:
    value: object = _MISSING
    restore: bool = False
    conflict: bool = False


def _field(
    field: str,
    owner: Owner,
    sides: _Sides,
    owners: Owners,
    depends: tuple[str, ...],
) -> _FieldDecision:
    user_changed = _changed(sides.base, sides.user, field)
    app_changed = _changed(sides.base, sides.app, field)
    if not user_changed and not app_changed:
        return _FieldDecision()
    if user_changed and not app_changed:
        return _user_only(field, owner, sides, owners, depends)
    if app_changed and not user_changed:
        if owner is Owner.USER:
            return _FieldDecision()
        return _FieldDecision(value=_value(sides.app, field))
    return _both(field, owner, sides, owners, depends)


def _user_only(
    field: str,
    owner: Owner,
    sides: _Sides,
    owners: Owners,
    depends: tuple[str, ...],
) -> _FieldDecision:
    if owner is Owner.APP:
        return _FieldDecision(value=_value(sides.base, field), restore=True)
    issue = _issue(field, sides.base, sides.user, owners, depends)
    if issue is not None:
        return _FieldDecision(value=_value(sides.base, field), restore=True)
    return _FieldDecision(value=_value(sides.user, field))


def _both(
    field: str,
    owner: Owner,
    sides: _Sides,
    owners: Owners,
    depends: tuple[str, ...],
) -> _FieldDecision:
    if owner is Owner.USER:
        return _FieldDecision(value=_value(sides.user, field), conflict=True)
    issue = _issue(field, sides.base, sides.user, owners, depends)
    if owner is Owner.SHARED and issue is None:
        return _FieldDecision(value=_value(sides.user, field), conflict=True)
    return _FieldDecision(value=_value(sides.app, field), restore=True, conflict=True)


def _issue(field: str, base: Note, user: Note, owners: Owners, depends: tuple[str, ...]) -> str | None:
    if field != "status":
        return None
    source = _status(base.status)
    target = _status(user.status)
    issue = "NODE_TRANSITION_INVALID" if source is None or target is None else None
    if issue is None and source is not None and target is not None:
        issue = human_transition_issue(source, target, review_required=base.review_required)
    unmet = _unmet(depends, owners)
    blocked = target is NodeStatus.DONE or target is NodeStatus.IN_PROGRESS
    if issue is None and blocked and unmet:
        issue = "NODE_NOT_READY"
    held = target in {NodeStatus.PENDING, NodeStatus.CANCELLED}
    if issue is None and held and owners.leased_by_other and not owners.release_confirmed:
        issue = "LEASE_HELD"
    return issue


def _depends_issue(raw: tuple[str, ...], owners: Owners) -> tuple[tuple[str, ...] | None, str | None]:
    resolved: list[str] = []
    for item in raw:
        key = resolve_ref(item)
        if key is None or key not in owners.known_keys:
            return None, "NODE_NOT_FOUND"
        resolved.append(key)
    keys = tuple(resolved)
    cycle = _cycle(owners.node_key, keys, owners)
    if cycle is not None:
        return None, "PLAN_CYCLE"
    return keys, None


def _cycle(node_key: str, depends: tuple[str, ...], owners: Owners) -> tuple[str, ...] | None:
    plan = Plan()
    known = set(owners.known_keys)
    known.add(node_key)
    for key in sorted(known):
        _ = plan.add(NodeSpec(key=key, created_at=_EPOCH))
    for node, blocker in owners.edges:
        if node == node_key or node not in known or blocker not in known:
            continue
        plan.depend(node, blocker)
    try:
        for blocker in depends:
            plan.depend(node_key, blocker)
    except DomainError as exc:
        if exc.name == "PLAN_CYCLE":
            return exc.extra
        raise
    return plan.cycle_path()


def _unmet(depends: tuple[str, ...], owners: Owners) -> tuple[str, ...]:
    status = dict(owners.blocker_status)
    return tuple(key for key in depends if status.get(key) != NodeStatus.DONE.value)


def _status(value: str) -> NodeStatus | None:
    try:
        return NodeStatus(value)
    except ValueError:
        return None


def _changed(left: Note, right: Note, field: str) -> bool:
    return _value(left, field) != _value(right, field)


def _value(note: Note, field: str) -> object:
    return _as_dict(note)[field]


def _from_dict(values: dict[str, object]) -> Note:
    return Note(
        status=_text(values, "status"),
        owner_agent=_text(values, "owner_agent"),
        depends_on=_texts(values, "depends_on"),
        paths=_texts(values, "paths"),
        acceptance=_texts(values, "acceptance"),
        review_required=_flag(values, "review_required"),
        tags=_texts(values, "tags"),
        lease=_text(values, "lease"),
        updated=_text(values, "updated"),
        title=_text(values, "title"),
        objective=_text(values, "objective"),
    )


def _as_dict(note: Note) -> dict[str, object]:
    return {
        "status": note.status,
        "owner_agent": note.owner_agent,
        "depends_on": _join(note.depends_on),
        "paths": _join(note.paths),
        "acceptance": _join(note.acceptance),
        "review_required": note.review_required,
        "tags": _join(note.tags),
        "lease": note.lease,
        "updated": note.updated,
        "title": note.title,
        "objective": note.objective,
    }


def _text(values: dict[str, object], field: str) -> str:
    value = values[field]
    if isinstance(value, str):
        return value
    message = f"bad vault field {field}"
    raise TypeError(message)


def _texts(values: dict[str, object], field: str) -> tuple[str, ...]:
    value = values[field]
    if isinstance(value, str):
        if value == "":
            return ()
        return tuple(value.split("\x1e"))
    message = f"bad vault field {field}"
    raise TypeError(message)


def _join(items: tuple[str, ...]) -> str:
    return "\x1e".join(items)


def _flag(values: dict[str, object], field: str) -> bool:
    value = values[field]
    if isinstance(value, bool):
        return value
    message = f"bad vault field {field}"
    raise TypeError(message)
