"""Worktree names, dirtiness, and the close decision. Git itself runs later."""

from dataclasses import dataclass
from enum import StrEnum

from aconsole.core.domain.errors import DomainError

_BRANCH_TAKEN = 2010
_SLUG_LIMIT = 40
_ID_LIMIT = 26


class WorkspaceMode(StrEnum):
    NEW_WORKTREE = "new_worktree"
    MAIN_CHECKOUT = "main_checkout"
    EXISTING_WORKTREE = "existing_worktree"


class CloseChoice(StrEnum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass(frozen=True, kw_only=True)
class Dirt:
    modified: int = 0
    staged: int = 0
    untracked: int = 0


@dataclass(frozen=True, kw_only=True)
class CloseDecision:
    closed: bool
    needs_decision: bool
    remove_worktree: bool
    delete_branch: bool
    force: bool
    dirty_summary: str | None


def path_segment(value: str) -> str:
    """One path piece: no slashes, no dot-dot."""
    cleaned: list[str] = []
    for char in value:
        if char.isalnum() or char in "-_":
            cleaned.append(char)
        else:
            cleaned.append("-")
    segment = "".join(cleaned).strip("-")
    if segment in {"", ".", ".."}:
        return "item"
    return segment


def slug(text: str) -> str:
    """Branch slug: lowercase words, dashes, at most 40 characters."""
    lowered: list[str] = []
    for char in text.lower():
        if char.isalnum():
            lowered.append(char)
        else:
            lowered.append("-")
    compact = "-".join(part for part in "".join(lowered).split("-") if part)
    trimmed = compact[:_SLUG_LIMIT].strip("-")
    return trimmed or "tab"


def branch_name(title: str, tab_id: str, taken: frozenset[str] | None = None) -> str:
    """`ac/<slug>-<id>`, with a numeric suffix while the name is already taken."""
    if taken is None:
        used: frozenset[str] = frozenset()
    else:
        used = taken
    ident = path_segment(tab_id).lower()[:_ID_LIMIT]
    stem = f"ac/{slug(title)}-{ident}"
    if stem not in used:
        return stem
    number = 2
    while f"{stem}-{number}" in used:
        number += 1
    return f"{stem}-{number}"


def claim_branch(name: str, taken: frozenset[str]) -> str:
    """Use an existing branch name only when no other worktree has it."""
    if name in taken:
        raise DomainError(code=_BRANCH_TAKEN, name="BRANCH_ALREADY_CHECKED_OUT", retryable=False, extra=(name,))
    return name


def worktree_path(root: str, repo: str, tab_id: str) -> str:
    """`~/.local/share/aconsole/worktrees/<repo>/<tab>`."""
    base = root.rstrip("/")
    return f"{base}/{path_segment(repo)}/{path_segment(tab_id)}"


def is_dirty(facts: Dirt) -> bool:
    return facts.modified > 0 or facts.staged > 0 or facts.untracked > 0


def dirty_summary(facts: Dirt) -> str | None:
    if not is_dirty(facts):
        return None
    parts: list[str] = []
    if facts.modified > 0:
        parts.append(f"{facts.modified} изменено")
    if facts.staged > 0:
        parts.append(f"{facts.staged} в индексе")
    if facts.untracked > 0:
        parts.append(f"{facts.untracked} новых")
    return ", ".join(parts)


def decide_close(
    facts: Dirt,
    choice: CloseChoice | None,
    *,
    mode: WorkspaceMode,
    branch: str | None,
) -> CloseDecision:
    """Dirty tree asks the user. A clean tree we created is removed. Never `--force`."""
    summary = dirty_summary(facts)
    if is_dirty(facts) and choice is None:
        return CloseDecision(
            closed=False,
            needs_decision=True,
            remove_worktree=False,
            delete_branch=False,
            force=False,
            dirty_summary=summary,
        )
    remove = choice is CloseChoice.REMOVE or (choice is None and not is_dirty(facts))
    if mode is not WorkspaceMode.NEW_WORKTREE:
        remove = False
    delete = remove and branch is not None and branch.startswith("ac/")
    closed = choice is not None or not is_dirty(facts)
    return CloseDecision(
        closed=closed,
        needs_decision=False,
        remove_worktree=remove,
        delete_branch=delete,
        force=False,
        dirty_summary=summary,
    )
