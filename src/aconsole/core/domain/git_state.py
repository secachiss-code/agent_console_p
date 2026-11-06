"""What Git's facts mean for a project's tabs. The commands live in infrastructure.

`behind`, `dirty`, and a conflict never imply one another, and a merge probe covers commits only.
"""

import re
from dataclasses import dataclass

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import path_segment

_STATE_STALE = 3024
_BUSY = 3025
_IN_PROGRESS = 3026
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_OID = re.compile(r"[0-9a-f]{40,64}")
_CHECKPOINT = re.compile(r"refs/aconsole/checkpoints/([^/]+)/([1-9][0-9]*)")
# The agent may touch files in these. Waiting for input means its turn ended.
_WRITING = frozenset({TabStatus.STARTING, TabStatus.WORKING, TabStatus.WAITING_APPROVAL, TabStatus.UNKNOWN})


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckpointRefName:
    segment: str
    number: int

    @property
    def checkpoint_id(self) -> str:
        return f"{self.segment}-{self.number}"


def checkpoint_ref_name(ref: str) -> CheckpointRefName | None:
    found = _CHECKPOINT.fullmatch(ref)
    if found is None:
        return None
    return CheckpointRefName(segment=found.group(1), number=int(found.group(2)))


def tab_segment(tab_id: str) -> str:
    return path_segment(tab_id)


def blocks_writes(status: TabStatus | None) -> bool:
    """True while an agent may be changing this worktree. No open tab means no agent."""
    return status in _WRITING


def require_oid(value: str) -> str:
    """A full object id. Anything else could be read by git as an option or a revision expression."""
    if _OID.fullmatch(value) is None:
        message = "not a full object id"
        raise ValueError(message)
    return value


def require_idle(status: TabStatus | None) -> None:
    if blocks_writes(status):
        raise DomainError(code=_BUSY, name="WORKTREE_BUSY", retryable=True)


def require_nothing_in_progress(in_progress: str | None) -> None:
    if in_progress is not None:
        raise DomainError(code=_IN_PROGRESS, name="GIT_OPERATION_IN_PROGRESS", retryable=False, extra=(in_progress,))


def require_fresh(*, expected_head: str | None, head: str | None, expected_index: str, index: str) -> None:
    """The user acted on what they were shown. If that moved, they must look again."""
    if expected_head != head or expected_index != index:
        raise DomainError(code=_STATE_STALE, name="GIT_STATE_STALE", retryable=True)


def ref_path(ref: str) -> str:
    """The file in a fact's code ref: `src/a.py:10` and `src/a.py#name` both mean `src/a.py`."""
    return ref.partition("#")[0].partition(":")[0].removeprefix("./").rstrip("/")


def touched_refs(refs: tuple[str, ...], changed: tuple[str, ...]) -> tuple[str, ...]:
    """Changed files that a ref names, or that lie under a directory it names."""
    wanted = tuple(path for path in (ref_path(ref) for ref in refs) if path)
    return tuple(path for path in changed if any(path == item or path.startswith(f"{item}/") for item in wanted))


def clean_for_terminal(text: str) -> str:
    """Text bound for a terminal. An escape in a diff must not end the paste or move the cursor."""
    return _CONTROL.sub("", text)


def count_patch_files(patch: str) -> int:
    return sum(1 for line in patch.splitlines() if line.startswith("diff --git "))


def choose_base(default_branch: str | None, branches: frozenset[str], remote_head: str | None) -> str | None:
    """The project's own default branch when it exists, then what the remote says. Nothing is assumed to be `main`."""
    for name in (default_branch, remote_head):
        if name is not None and name in branches:
            return name
    return None
